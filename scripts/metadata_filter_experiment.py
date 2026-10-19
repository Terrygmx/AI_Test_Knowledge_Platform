#!/usr/bin/env python3
"""Phase 10 Metadata Filter 实验。

默认使用 Phase 10 的 22 条 Query / 47 个 ``entity+heading`` Chunk，并比较：

    none / pre / post / during

真实模式只允许写入名称包含 ``filter`` 的隔离 PostgreSQL 库，使用 pgvector
SQL 预过滤后检索。实验不改统一 ``/search`` 或 RAG 的公开契约。

示例：
    uv run python scripts/metadata_filter_experiment.py --dry-run
    uv run python scripts/metadata_filter_experiment.py --offline \
        --output results/metadata-filter-phase10-offline.json
    uv run python scripts/metadata_filter_experiment.py \
        --database-url postgresql+psycopg://.../aitkp_filter \
        --output results/metadata-filter-phase10.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    select,
    text,
)
from sqlalchemy.engine import Engine

from app.core.config import settings
from app.core.logging import get_logger
from app.evaluation.dataset import ChunkingDataset, load_dataset
from app.evaluation.lexical_experiment import retrieve_chunks
from app.evaluation.metadata_filter_experiment import (
    FilterCandidate,
    evaluate_metadata_filters,
)
from app.models.types import JSONMap
from app.retrieval.embedding import get_embedding_client
from app.retrieval.filters import apply_metadata_filters_to_table

DEFAULT_DATASET = Path("eval/fixtures/phase10_simulated.json")
TABLE_NAME = "phase10_filter_chunks"
logger = get_logger(__name__)


def _git_output(arguments: list[str]) -> str | None:
    try:
        return (
            subprocess.check_output(
                ["git", *arguments],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            or None
        )
    except (OSError, subprocess.CalledProcessError):
        return None


def _redact_command(command: list[str]) -> list[str]:
    redacted: list[str] = []
    for item in command:
        if item.startswith("postgresql+") and "://" in item and "@" in item:
            scheme, remainder = item.split("://", 1)
            credentials, host = remainder.split("@", 1)
            username = credentials.split(":", 1)[0]
            item = f"{scheme}://{username}:***@{host}"
        redacted.append(item)
    return redacted


def _candidate_digest(documents: list[Any]) -> str:
    payload = [
        [
            item.source_type,
            item.source_id,
            item.chunk_index,
            item.content,
            item.meta,
        ]
        for item in documents
    ]
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def build_engine(database_url: str, *, allow_sqlite: bool = False) -> Engine:
    """真实实验只写名称包含 ``filter`` 的 PostgreSQL 库。"""
    if database_url.startswith("sqlite"):
        if not allow_sqlite:
            raise ValueError("默认拒绝写入 SQLite；离线演练请显式 allow_sqlite=True")
        return create_engine(database_url)
    database_name = database_url.rsplit("/", 1)[-1].split("?", 1)[0]
    if "filter" not in database_name:
        raise ValueError(f"实验数据库名必须包含 filter：{database_name}")
    return create_engine(database_url, pool_pre_ping=True)


def _verify_database(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        raise ValueError("真实 Metadata Filter 实验只支持 PostgreSQL")
    with engine.connect() as connection:
        vector = connection.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        ).scalar()
    if not vector:
        raise ValueError("目标数据库未安装 pgvector 扩展")


def _create_table(engine: Engine, dimension: int) -> Table:
    from pgvector.sqlalchemy import Vector

    metadata = MetaData()
    table = Table(
        TABLE_NAME,
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("source_type", String(32), nullable=False),
        Column("source_id", String(64), nullable=False),
        Column("chunk_index", Integer, nullable=False),
        Column("content", Text, nullable=False),
        Column("metadata", JSONMap, nullable=False),
        Column("embedding", Vector(dimension), nullable=False),
    )
    table.drop(engine, checkfirst=True)
    table.create(engine)
    return table


def _filter_sql(
    table: Table,
    filters: dict[str, object],
    *,
    dialect_name: str,
):
    """实验表过滤直接复用生产过滤实现，避免两套 SQL 语义漂移。"""
    return apply_metadata_filters_to_table(
        select(table),
        table.c.metadata,
        filters,
        dialect_name=dialect_name,
    )


def _manifest(
    dataset: ChunkingDataset,
    result,
    *,
    dataset_path: str | Path,
    started_at: datetime,
    finished_at: datetime,
    command: list[str],
    result_path: str | Path,
    top_k: int,
    candidate_k: int,
    strategy: str,
    rrf_k: int,
    embedding_model: str,
    embedding_dimension: int,
    embedding_batch_size: int,
    embedding_timeout_seconds: float,
    embedding_max_retries: int,
    candidate_sha256: str,
    database_url: str,
    mode: str,
) -> dict[str, Any]:
    if top_k < 1 or candidate_k < top_k:
        raise ValueError("必须满足 candidate_k >= top_k >= 1")
    if rrf_k < 1:
        raise ValueError("rrf_k 必须 >= 1")
    if strategy not in {"vector", "keyword", "rrf"}:
        raise ValueError("strategy 必须是 vector、keyword 或 rrf")
    database_name = database_url.rsplit("/", 1)[-1].split("?", 1)[0]
    return {
        "phase": 10,
        "manifest_schema_version": 1,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "git_commit": _git_output(["rev-parse", "HEAD"]) or "unknown",
        "git_commit_role": "generation",
        "git_dirty": bool(_git_output(["status", "--porcelain"])),
        "command": _redact_command(command),
        "result_path": str(result_path),
        "mode": mode,
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
        "retrieval": {
            "strategy": strategy,
            "candidate_k": candidate_k,
            "top_k": top_k,
            "rrf_k": rrf_k,
            "variants": ["none", "pre", "post", "during"],
            "variant_execution": {
                "none": "unfiltered in-memory ranking",
                "pre": "SQL WHERE before ranking",
                "post": "unfiltered Top-K followed by filtering without refill",
                "during": "same filtered set as pre; not an independent HNSW ANN run",
            },
            "ann_index": None,
            "latency_scope": "candidate ranking and fusion only; excludes query embedding",
            "filter_semantics": {
                "scalar": "exact",
                "arrays": "contains-any",
                "combine": "and",
                "missing": "no-match",
            },
        },
        "results": [
            {
                "name": variant.name,
                "metrics": variant.metrics,
                "query_p50_seconds": variant.query_p50_seconds,
                "query_p95_seconds": variant.query_p95_seconds,
                "candidate_counts_before": variant.candidate_counts_before,
                "candidate_counts_after": variant.candidate_counts_after,
                "result_counts": variant.result_counts,
                "empty_result_queries": variant.empty_result_queries,
                "gold_filtered_out_queries": variant.gold_filtered_out_queries,
            }
            for variant in result.variants
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 10 Metadata Filter 实验")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument(
        "--database-url",
        default=f"{settings.database_url.rsplit('/', 1)[0]}/aitkp_filter",
    )
    parser.add_argument("--output", default="results/metadata-filter-phase10.json")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-k", type=int, default=20)
    parser.add_argument("--strategy", choices=["vector", "keyword", "rrf"], default="rrf")
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--offline", action="store_true", help="不连接模型 / 数据库的契约演练")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.top_k < 1 or args.candidate_k < args.top_k:
        parser.error("必须满足 --candidate-k >= --top-k >= 1")
    if args.rrf_k < 1:
        parser.error("--rrf-k 必须 >= 1")

    dataset = load_dataset(args.dataset)
    documents = retrieve_chunks(dataset)
    if args.dry_run:
        print(
            f"dataset={args.dataset} sha256={dataset.dataset_sha256[:12]} "
            f"queries={len(dataset.queries)} chunks={len(documents)} "
            f"candidate_k={args.candidate_k} top_k={args.top_k} "
            f"strategy={args.strategy} mode={'offline' if args.offline else 'postgresql'}"
        )
        return

    started_at = datetime.now(UTC)
    if args.offline:
        # 离线模式只证明过滤语义与候选饥饿；向量是固定占位，不代表语义质量。
        candidates = [
            FilterCandidate(
                source_type=item.source_type,
                source_id=item.source_id,
                chunk_index=item.chunk_index,
                content=item.content,
                embedding=(1.0, *([0.0] * 1023)),
                meta=item.meta or {},
            )
            for item in documents
        ]
        result = evaluate_metadata_filters(
            dataset,
            candidates,
            [(1.0, *([0.0] * 1023))] * len(dataset.queries),
            top_k=args.top_k,
            candidate_k=args.candidate_k,
            strategy=args.strategy,
            rrf_k=args.rrf_k,
        )
        mode = "offline"
        dimension = 1024
    else:
        engine = build_engine(args.database_url)
        _verify_database(engine)
        embedding = get_embedding_client()
        dimension = embedding.dimension
        table = _create_table(engine, dimension)
        index_started = time.perf_counter()
        embeddings = embedding.embed_texts([item.content for item in documents])
        if len(embeddings) != len(documents):
            raise ValueError("Embedding 数量与 Chunk 数不一致")
        if any(len(vector) != dimension for vector in embeddings):
            raise ValueError("Embedding 维度与客户端声明不一致")
        with engine.begin() as connection:
            connection.execute(
                table.insert(),
                [
                    {
                        "source_type": item.source_type,
                        "source_id": item.source_id,
                        "chunk_index": item.chunk_index,
                        "content": item.content,
                        "metadata": item.meta or {},
                        "embedding": vector,
                    }
                    for item, vector in zip(documents, embeddings, strict=True)
                ],
            )
        index_seconds = time.perf_counter() - index_started
        query_started = time.perf_counter()
        query_embeddings = [embedding.embed_query(query.query) for query in dataset.queries]
        query_embedding_seconds = time.perf_counter() - query_started
        candidates = [
            FilterCandidate(
                source_type=item.source_type,
                source_id=item.source_id,
                chunk_index=item.chunk_index,
                content=item.content,
                embedding=tuple(vector),
                meta=item.meta or {},
            )
            for item, vector in zip(documents, embeddings, strict=True)
        ]

        # 预过滤是“SQL WHERE 后直接在该子集上排序”，不是先读全表再在 Python 里
        # 过滤。这里显式从数据库取回过滤后的候选，确保实验与真实执行路径一致。
        def prefilter_rows(filters: dict[str, object]) -> set[tuple[str, str, int]]:
            stmt = _filter_sql(table, filters, dialect_name="postgresql")
            stmt = stmt.with_only_columns(
                table.c.source_type,
                table.c.source_id,
                table.c.chunk_index,
            )
            with engine.connect() as connection:
                return {
                    (row.source_type, row.source_id, row.chunk_index)
                    for row in connection.execute(stmt)
                }

        def sql_prefilter(filters):
            allowed = prefilter_rows(filters)
            return [candidate for candidate in candidates if candidate.key in allowed]

        result = evaluate_metadata_filters(
            dataset,
            candidates,
            query_embeddings,
            top_k=args.top_k,
            candidate_k=args.candidate_k,
            strategy=args.strategy,
            rrf_k=args.rrf_k,
            prefilter=sql_prefilter,
        )
        mode = "postgresql"

    manifest = _manifest(
        dataset,
        result,
        dataset_path=args.dataset,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        command=[sys.executable, *sys.argv],
        result_path=args.output,
        top_k=args.top_k,
        candidate_k=args.candidate_k,
        strategy=args.strategy,
        rrf_k=args.rrf_k,
        embedding_model=(
            settings.embedding_model if mode == "postgresql" else "offline-placeholder"
        ),
        embedding_dimension=dimension,
        embedding_batch_size=settings.embedding_batch_size,
        embedding_timeout_seconds=settings.embedding_timeout,
        embedding_max_retries=settings.embedding_max_retries,
        candidate_sha256=_candidate_digest(documents),
        database_url=args.database_url if mode == "postgresql" else "offline",
        mode=mode,
    )
    if mode == "postgresql":
        manifest["index_seconds"] = index_seconds
        manifest["query_embedding_seconds"] = query_embedding_seconds
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for variant in result.variants:
        metrics = variant.metrics
        print(
            f"{variant.name:7s} R@5={metrics.get('recall@5', 0):.3f} "
            f"MRR={metrics['mrr']:.3f} "
            f"empty={variant.empty_result_queries} "
            f"p95={variant.query_p95_seconds:.4f}s"
        )
    print(f"manifest={output}")


if __name__ == "__main__":
    main()
