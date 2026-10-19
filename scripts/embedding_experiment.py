#!/usr/bin/env python3
"""Phase 7 Embedding 模型对比实验。

示例：
    uv run python scripts/embedding_experiment.py --dry-run
    uv run python scripts/embedding_experiment.py \
        --database-url postgresql+psycopg://.../aitkp_embedding \
        --models bge-m3-mlx-4bit:1024,embeddinggemma-2-oQ8e:768 \
        --output results/embedding-2026-10-20.json

实验设计：
- 固定 Phase 6 的 ``entity+heading`` Chunk 与 40 条 Query，只替换 Embedding；
- 每个模型建立独立的 pgvector 表，按模型真实维度建列，不截断 / 补零；
- 记录索引、Query Embedding、pgvector 检索与端到端延迟；
- manifest 记录模型、维度、数据集 SHA、git 状态和脱敏命令。

安全约束：只允许写入库名包含 ``embedding`` 的 PostgreSQL 数据库。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.config import Settings, settings
from app.core.db import Base
from app.core.logging import get_logger
from app.evaluation.dataset import ChunkingDataset, load_dataset
from app.evaluation.embedding_experiment import (
    EmbeddingConfig,
    EmbeddingResult,
    build_engine,
    chunk_length_summary,
    clear_experiment_tables,
    parse_model_specs,
    run_embedding_experiment,
)
from app.retrieval.embedding import OpenAICompatibleEmbeddingClient
from app.schemas.knowledge_chunk import KnowledgeChunkCreate
from scripts.chunking_experiment import (
    StrategyConfig,
    _clear_experiment_data,
    _document_chunks,
    _entity_chunks,
    _seed_entities,
)

DEFAULT_DATASET = Path("eval/fixtures/phase6_simulated.json")
DEFAULT_MODELS = (
    "bge-m3-mlx-4bit:1024,"
    "embeddinggemma-2-oQ8e:768,"
    "all-MiniLM-L6-v2:384"
)
logger = get_logger(__name__)


def _baseline_chunks(
    session: Session,
    dataset: ChunkingDataset,
) -> list[KnowledgeChunkCreate]:
    """固定 Phase 6 的 entity+heading 基线，不再改变切分。"""
    # 脚本必须可重复运行；否则第二次执行会在业务编号唯一键上失败。
    _clear_experiment_data(session)
    _seed_entities(session, dataset)
    return [
        *_entity_chunks(session),
        *_document_chunks(
            dataset,
            StrategyConfig("entity+heading", "entity"),
            embedding=_DummyEmbedding(),
        ),
    ]


class _DummyEmbedding:
    """entity+heading 切分不需要 Embedding，只为满足函数签名。"""

    dimension = 1024

    def embed_texts(self, texts):
        raise AssertionError("entity+heading 基线不应调用 Embedding")

    def embed_query(self, text):
        raise AssertionError("entity+heading 基线不应调用 Embedding")


def _experiment_settings(config: EmbeddingConfig) -> Settings:
    """复制全部配置，只覆盖模型名与维度，避免遗漏超时等字段。"""
    return settings.model_copy(
        update={
            "embedding_model": config.model,
            "embedding_dim": config.dimension,
        }
    )


def _client_for(config: EmbeddingConfig) -> OpenAICompatibleEmbeddingClient:
    client_settings = _experiment_settings(config)
    return OpenAICompatibleEmbeddingClient(
        client_settings,
        allow_non_production_dimension=True,
    )


def _verify_experiment_database(engine: Engine) -> None:
    with engine.connect() as connection:
        if connection.dialect.name != "postgresql":
            raise ValueError("真实实验只支持 PostgreSQL")
        vector = connection.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        ).scalar()
        if not vector:
            raise ValueError("目标数据库未安装 pgvector 扩展")


def _fetch_model_metadata(
    *,
    base_url: str,
    api_key: str,
    model_names: list[str],
    timeout: float,
) -> dict[str, Any]:
    """记录服务端实际声明的模型元数据；接口不可用时诚实标记。"""
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
    except Exception as exc:  # noqa: BLE001 - 元数据不可用不应阻断实验
        logger.warning("读取 Embedding 服务模型元数据失败：%s", type(exc).__name__)
        return {"status": "unavailable", "error": type(exc).__name__}

    models = {name: entries.get(name) for name in model_names}
    missing = [name for name, metadata in models.items() if metadata is None]
    return {
        "status": "partial" if missing else "available",
        "models": models,
        "missing_models": missing,
    }


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


def _git_output(arguments: list[str]) -> str | None:
    output = subprocess.check_output(
        ["git", *arguments],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()
    return output or None


def _manifest(
    dataset: ChunkingDataset,
    results: list[EmbeddingResult],
    configs: list[EmbeddingConfig],
    chunks: list[KnowledgeChunkCreate],
    *,
    database_url: str,
    dataset_path: str | Path,
    started_at: datetime,
    finished_at: datetime,
    command: list[str],
    run_id: str,
    cleared_tables: list[str],
    model_metadata: dict[str, Any],
) -> dict[str, Any]:
    database_name = database_url.rsplit("/", 1)[-1].split("?", 1)[0]
    git_commit = "unknown"
    git_dirty = False
    try:
        git_commit = _git_output(["rev-parse", "HEAD"]) or "unknown"
        git_dirty = bool(_git_output(["status", "--porcelain"]))
    except (OSError, subprocess.CalledProcessError):
        pass

    return {
        "phase": 7,
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "command": _redact_command(command),
        "dataset": {
            "path": str(dataset_path),
            "sha256": dataset.dataset_sha256,
            "queries": len(dataset.queries),
            "answerable": sum(query.answerable for query in dataset.queries),
            "unanswerable": sum(not query.answerable for query in dataset.queries),
        },
        "chunking_baseline": {
            "strategy": "entity+heading",
            **chunk_length_summary(chunks),
        },
        "environment": {
            "database_name": database_name,
            "embedding_base_url": settings.embedding_base_url,
            "embedding_batch_size": settings.embedding_batch_size,
            "embedding_timeout_seconds": settings.embedding_timeout,
            "embedding_max_retries": settings.embedding_max_retries,
        },
        "embedding_service": {
            "model_metadata": model_metadata,
        },
        "cleared_experiment_tables": cleared_tables,
        "models": [
            {
                "name": config.name,
                "model": config.model,
                "dimension": config.dimension,
            }
            for config in configs
        ],
        "results": [
            {
                "name": result.name,
                "model": result.model,
                "dimension": result.dimension,
                "table_name": result.table_name,
                "chunk_count": result.chunk_count,
                "batch_count": result.batch_count,
                "warmup_seconds": result.warmup_seconds,
                "content_embedding_seconds": result.content_embedding_seconds,
                "write_seconds": result.write_seconds,
                "index_seconds": result.index_seconds,
                "query_embedding_p50_seconds": result.query_embedding_p50_seconds,
                "query_embedding_p95_seconds": result.query_embedding_p95_seconds,
                "search_p50_seconds": result.search_p50_seconds,
                "search_p95_seconds": result.search_p95_seconds,
                "query_total_p50_seconds": result.query_total_p50_seconds,
                "query_total_p95_seconds": result.query_total_p95_seconds,
                "metrics": result.metrics,
            }
            for result in results
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 7 Embedding 模型对比")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument(
        "--database-url",
        default=f"{settings.database_url.rsplit('/', 1)[0]}/aitkp_embedding",
    )
    parser.add_argument("--models", default=DEFAULT_MODELS)
    parser.add_argument("--output", default="results/embedding-experiment.json")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    configs = parse_model_specs(args.models)
    dataset = load_dataset(args.dataset)
    if args.top_k < 1:
        parser.error("--top-k 必须 >= 1")
    if args.dry_run:
        print(
            f"dataset={args.dataset} sha256={dataset.dataset_sha256[:12]} "
            f"queries={len(dataset.queries)} "
            f"models={','.join(f'{item.model}:{item.dimension}' for item in configs)} "
            "chunking=entity+heading"
        )
        return

    started_at = datetime.now(UTC)
    run_id = uuid.uuid4().hex
    engine = build_engine(args.database_url)
    _verify_experiment_database(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        chunks = _baseline_chunks(session, dataset)
    if not chunks:
        raise SystemExit("entity+heading 基线没有生成任何 Chunk")

    cleared_tables = clear_experiment_tables(engine)
    clients = [_client_for(config) for config in configs]
    results = run_embedding_experiment(
        engine,
        dataset,
        chunks,
        configs,
        clients,
        top_k=args.top_k,
        batch_size=settings.embedding_batch_size,
    )
    model_metadata = _fetch_model_metadata(
        base_url=settings.embedding_base_url,
        api_key=settings.embedding_api_key,
        model_names=[config.model for config in configs],
        timeout=settings.embedding_timeout,
    )
    manifest = _manifest(
        dataset,
        results,
        configs,
        chunks,
        database_url=args.database_url,
        dataset_path=args.dataset,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        command=[sys.executable, *sys.argv],
        run_id=run_id,
        cleared_tables=cleared_tables,
        model_metadata=model_metadata,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    for result in results:
        metrics = result.metrics
        print(
            f"{result.name:26s} dim={result.dimension:4d} "
            f"R@5={metrics['recall@5']:.3f} R@10={metrics['recall@10']:.3f} "
            f"MRR={metrics['mrr']:.3f} "
            f"index={result.index_seconds:.1f}s "
            f"query_p95={result.query_total_p95_seconds:.3f}s"
        )
    print(f"manifest={output}")


if __name__ == "__main__":
    main()
