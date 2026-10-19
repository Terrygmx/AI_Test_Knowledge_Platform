#!/usr/bin/env python3
"""Phase 9 Hybrid Search 实验。

比较 Vector、Keyword、Weighted、RRF，固定同一份 Chunk 与 Gold Set。
默认使用 Phase 8 的 24 条 Query 与 47 个 Chunk；真实运行只在库名包含
``hybrid`` 的 PostgreSQL 中创建隔离实验表，不写入生产表。

示例：
    uv run python scripts/hybrid_experiment.py --dry-run
    uv run python scripts/hybrid_experiment.py \
        --database-url postgresql+psycopg://.../aitkp_hybrid \
        --output results/hybrid-phase9.json
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    text,
)
from sqlalchemy.engine import Engine

from app.core.config import settings
from app.core.logging import get_logger
from app.evaluation.dataset import ChunkingDataset, load_dataset
from app.evaluation.hybrid_experiment import (
    HybridCandidate,
    HybridExperimentResult,
    evaluate_hybrid,
)
from app.evaluation.lexical_experiment import k_values_for, retrieve_chunks
from app.retrieval.embedding import get_embedding_client

DEFAULT_DATASET = Path("eval/fixtures/phase8_simulated.json")
TABLE_NAME = "phase9_hybrid_chunks"
logger = get_logger(__name__)


def build_engine(database_url: str, *, allow_sqlite: bool = False) -> Engine:
    """真实实验只写名称包含 hybrid 的 PostgreSQL 库。"""
    if database_url.startswith("sqlite"):
        if not allow_sqlite:
            raise ValueError("默认拒绝写入 SQLite；离线演练需显式 allow_sqlite=True")
        return create_engine(database_url)
    database_name = database_url.rsplit("/", 1)[-1].split("?", 1)[0]
    if "hybrid" not in database_name:
        raise ValueError(f"实验数据库名必须包含 hybrid：{database_name}")
    return create_engine(database_url, pool_pre_ping=True)


def _verify_database(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        raise ValueError("真实 Hybrid 实验只支持 PostgreSQL")
    with engine.connect() as connection:
        vector = connection.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        ).scalar()
    if not vector:
        raise ValueError("目标数据库未安装 pgvector 扩展")


def _create_vector_table(engine: Engine, dimension: int) -> Table:
    metadata = MetaData()
    table = Table(
        TABLE_NAME,
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("source_type", String(32), nullable=False),
        Column("source_id", String(64), nullable=False),
        Column("chunk_index", Integer, nullable=False),
        Column("content", Text, nullable=False),
        Column("embedding", Vector(dimension), nullable=False),
    )
    table.drop(engine, checkfirst=True)
    table.create(engine)
    return table


def _candidate_digest(dataset: ChunkingDataset, documents: list[Any]) -> str:
    payload = {
        "dataset": dataset.dataset_sha256,
        "chunks": [
            [item.source_type, item.source_id, item.chunk_index, item.content]
            for item in documents
        ],
    }
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _git_output(arguments: list[str]) -> str | None:
    try:
        output = subprocess.check_output(
            ["git", *arguments],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return output or None


def _redact_command(command: list[str]) -> list[str]:
    """manifest 不应保存数据库密码。"""
    redacted: list[str] = []
    for item in command:
        if item.startswith("postgresql+") and "://" in item and "@" in item:
            scheme, remainder = item.split("://", 1)
            credentials, host = remainder.split("@", 1)
            username = credentials.split(":", 1)[0]
            item = f"{scheme}://{username}:***@{host}"
        redacted.append(item)
    return redacted


def _fetch_model_metadata(
    *,
    base_url: str,
    api_key: str,
    model_names: list[str],
    timeout: float,
) -> dict[str, Any]:
    """记录服务端声明的模型元数据；接口不可用时不让实验失败。"""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}/models",
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        entries = {
            str(item["id"]): {
                key: value
                for key, value in item.items()
                if key != "id" and isinstance(value, (str, int, float, bool))
            }
            for item in payload.get("data", [])
            if isinstance(item, dict) and item.get("id")
        }
    except Exception as exc:  # noqa: BLE001 - 元数据不可用不影响检索结果
        logger.warning("读取 Embedding 服务模型元数据失败：%s", type(exc).__name__)
        return {"status": "unavailable", "error": type(exc).__name__}

    models = {name: entries.get(name) for name in model_names}
    missing = [name for name, metadata in models.items() if metadata is None]
    return {
        "status": "partial" if missing else "available",
        "models": models,
        "missing_models": missing,
    }


def _manifest(
    dataset: ChunkingDataset,
    result: HybridExperimentResult,
    *,
    dataset_path: str | Path,
    started_at: datetime,
    finished_at: datetime,
    command: list[str],
    result_path: str | Path,
    candidate_k: int,
    top_k: int,
    selection: str,
    vector_weight: float,
    keyword_weight: float,
    rrf_k: int,
    embedding_model: str,
    embedding_dimension: int,
    embedding_batch_size: int,
    embedding_timeout_seconds: float,
    embedding_max_retries: int,
    model_metadata: dict[str, Any],
    candidate_sha256: str,
    database_url: str,
) -> dict[str, Any]:
    if top_k < 1 or candidate_k < top_k:
        raise ValueError("必须满足 candidate_k >= top_k >= 1")
    if selection not in {"all", "weighted", "rrf"}:
        raise ValueError("selection 必须是 all、weighted 或 rrf")
    if not math.isfinite(vector_weight) or not math.isfinite(keyword_weight):
        raise ValueError("通道权重必须是有限数")
    if vector_weight < 0 or keyword_weight < 0:
        raise ValueError("通道权重必须 >= 0")
    if rrf_k < 1:
        raise ValueError("rrf_k 必须 >= 1")
    if embedding_batch_size < 1:
        raise ValueError("embedding_batch_size 必须 >= 1")
    if embedding_timeout_seconds <= 0:
        raise ValueError("embedding_timeout_seconds 必须 > 0")
    if embedding_max_retries < 0:
        raise ValueError("embedding_max_retries 必须 >= 0")
    database_name = database_url.rsplit("/", 1)[-1].split("?", 1)[0]
    evaluated_variants = [channel.name for channel in result.channels]
    if not evaluated_variants:
        raise ValueError("至少需要评测一个通道")
    expected_variants = {
        "all": ["vector", "keyword", "weighted", "rrf"],
        "weighted": ["weighted"],
        "rrf": ["rrf"],
    }
    if evaluated_variants != expected_variants[selection]:
        raise ValueError(
            f"selection={selection} 与实际评测通道 {evaluated_variants} 不一致"
        )
    return {
        "phase": 9,
        "manifest_schema_version": 2,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "git_commit": _git_output(["rev-parse", "HEAD"]) or "unknown",
        # 产物入库后 HEAD 会前进；该字段只声明指标生成时的干净工作区。
        "git_commit_role": "generation",
        "git_dirty": bool(_git_output(["status", "--porcelain"])),
        "command": _redact_command(command),
        "result_path": str(result_path),
        "dataset": {
            "path": str(dataset_path),
            "sha256": dataset.dataset_sha256,
            "queries": len(dataset.queries),
            "answerable": sum(query.answerable for query in dataset.queries),
            "unanswerable": sum(not query.answerable for query in dataset.queries),
        },
        "chunks": {
            "count": result.chunk_count,
            "sha256": candidate_sha256,
            "strategy": "entity+heading",
        },
        "environment": {
            "database_name": database_name,
            "embedding_base_url": settings.embedding_base_url,
            "embedding_model": embedding_model,
            "embedding_dimension": embedding_dimension,
            "embedding_batch_size": embedding_batch_size,
            "embedding_timeout_seconds": embedding_timeout_seconds,
            "embedding_max_retries": embedding_max_retries,
        },
        "embedding_service": {"model_metadata": model_metadata},
        "retrieval": {
            "candidate_k": candidate_k,
            "top_k": top_k,
            "k_values": list(k_values_for(top_k)),
            "selection": selection,
            "evaluated_variants": evaluated_variants,
            "default_fusion": "rrf",
            "vector_weight": vector_weight,
            "keyword_weight": keyword_weight,
            "rrf_k": rrf_k,
            "normalization": "per-channel-min-max",
        },
        "index_seconds": result.index_seconds,
        "query_embedding_seconds": result.query_embedding_seconds,
        "results": [
            {
                "name": channel.name,
                "strategy": channel.strategy,
                "metrics": channel.metrics,
                "query_p50_seconds": channel.query_p50_seconds,
                "query_p95_seconds": channel.query_p95_seconds,
                "vector_candidate_hits": channel.vector_candidate_hits,
                "keyword_candidate_hits": channel.keyword_candidate_hits,
                "fused_candidates": channel.fused_candidates,
            }
            for channel in result.channels
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 9 Hybrid Search 实验")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument(
        "--database-url",
        default=f"{settings.database_url.rsplit('/', 1)[0]}/aitkp_hybrid",
    )
    parser.add_argument("--output", default="results/hybrid-phase9.json")
    parser.add_argument("--candidate-k", type=int, default=20)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument(
        "--fusion",
        dest="selection",
        choices=["all", "weighted", "rrf"],
        default="all",
        help="评测范围；all 比较四通道，weighted/rrf 只评测所选融合策略",
    )
    parser.add_argument("--vector-weight", type=float, default=1.0)
    parser.add_argument("--keyword-weight", type=float, default=1.0)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.top_k < 1 or args.candidate_k < args.top_k:
        parser.error("必须满足 --candidate-k >= --top-k >= 1")
    if args.selection == "weighted" and args.vector_weight + args.keyword_weight <= 0:
        parser.error("Weighted 的通道权重之和必须大于 0")
    if args.rrf_k < 1:
        parser.error("--rrf-k 必须 >= 1")

    dataset = load_dataset(args.dataset)
    documents = retrieve_chunks(dataset)
    if args.dry_run:
        print(
            f"dataset={args.dataset} sha256={dataset.dataset_sha256[:12]} "
            f"queries={len(dataset.queries)} chunks={len(documents)} "
            f"candidate_k={args.candidate_k} top_k={args.top_k} "
            f"fusion={args.selection} vector_weight={args.vector_weight} "
            f"keyword_weight={args.keyword_weight} rrf_k={args.rrf_k}"
        )
        return

    started_at = datetime.now(UTC)
    engine = build_engine(args.database_url)
    _verify_database(engine)

    embedding = get_embedding_client()
    target_dimension = embedding.dimension
    table = _create_vector_table(engine, target_dimension)
    contents = [item.content for item in documents]
    index_started = time.perf_counter()
    embeddings = embedding.embed_texts(contents)
    if len(embeddings) != len(documents):
        raise ValueError("Embedding 数量与 Chunk 数不一致")
    if any(len(vector) != target_dimension for vector in embeddings):
        raise ValueError("Embedding 维度与 settings 不一致")
    with engine.begin() as connection:
        connection.execute(
            table.insert(),
            [
                {
                    "source_type": item.source_type,
                    "source_id": item.source_id,
                    "chunk_index": item.chunk_index,
                    "content": item.content,
                    "embedding": vector,
                }
                for item, vector in zip(documents, embeddings, strict=True)
            ],
        )
    index_seconds = time.perf_counter() - index_started

    # BM25 使用同一批 Chunk；HybridCandidate 仅用于身份与正文，向量来自 pgvector。
    candidates = [
        HybridCandidate(
            source_type=item.source_type,
            source_id=item.source_id,
            chunk_index=item.chunk_index,
            content=item.content,
            embedding=tuple(vector),
        )
        for item, vector in zip(documents, embeddings, strict=True)
    ]
    query_started = time.perf_counter()
    query_embeddings = [embedding.embed_query(query.query) for query in dataset.queries]
    query_embedding_seconds = time.perf_counter() - query_started
    model_metadata = _fetch_model_metadata(
        base_url=settings.embedding_base_url,
        api_key=settings.embedding_api_key,
        model_names=[settings.embedding_model],
        timeout=settings.embedding_timeout,
    )
    result = evaluate_hybrid(
        dataset,
        candidates,
        query_embeddings,
        top_k=args.top_k,
        candidate_k=args.candidate_k,
        fusion=args.selection,
        vector_weight=args.vector_weight,
        keyword_weight=args.keyword_weight,
        rrf_k=args.rrf_k,
        engine=engine,
        vector_table=table,
        index_seconds=index_seconds,
        query_embedding_seconds=query_embedding_seconds,
    )
    manifest = _manifest(
        dataset,
        result,
        dataset_path=args.dataset,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        command=[sys.executable, *sys.argv],
        result_path=args.output,
        candidate_k=args.candidate_k,
        top_k=args.top_k,
        selection=args.selection,
        vector_weight=args.vector_weight,
        keyword_weight=args.keyword_weight,
        rrf_k=args.rrf_k,
        embedding_model=settings.embedding_model,
        embedding_dimension=target_dimension,
        embedding_batch_size=settings.embedding_batch_size,
        embedding_timeout_seconds=settings.embedding_timeout,
        embedding_max_retries=settings.embedding_max_retries,
        model_metadata=model_metadata,
        candidate_sha256=_candidate_digest(dataset, documents),
        database_url=args.database_url,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for channel in result.channels:
        metrics = channel.metrics
        recall = " ".join(
            f"R@{k}={metrics[f'recall@{k}']:.3f}"
            for k in k_values_for(args.top_k)
        )
        print(
            f"{channel.name:8s} {recall} MRR={metrics['mrr']:.3f} "
            f"p95={channel.query_p95_seconds:.4f}s"
        )
    print(f"manifest={output}")


if __name__ == "__main__":
    main()
