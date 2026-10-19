#!/usr/bin/env python3
"""Phase 6 Chunking 对比实验。

示例：
    uv run python scripts/chunking_experiment.py --dry-run
    uv run python scripts/chunking_experiment.py \
        --database-url postgresql+psycopg://.../aitkp_chunking \
        --output results/chunking-2026-10-20.json

安全约束：
- 只允许写入库名以 ``chunking`` 结尾的 PostgreSQL 数据库；
- 每个策略串行执行：清空 Chunk / 实体 → 导入同一语料 → 重新生成 Embedding
  → 按同一 Gold Set 计算 Recall@5 / Recall@10 / MRR；
- 默认 ``--dry-run``，不调用 OMLX，也不写数据库。

这是实验脚本而非生产导入入口：它会清空目标库中的实体和 Chunk。
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.db import Base
from app.evaluation.dataset import ChunkingDataset, load_dataset
from app.evaluation.metrics import RetrievalEvaluation, percentile
from app.ingestion.markdown import MarkdownParser
from app.models import Defect, Document, KnowledgeChunk, Requirement, TestCase
from app.retrieval.chunking import ChunkDraft, FixedChunker, SemanticChunker
from app.retrieval.embedding import EmbeddingClient, content_sha256, get_embedding_client
from app.schemas.knowledge_chunk import KnowledgeChunkCreate
from app.services.index_service import (
    case_to_chunk,
    defect_to_chunk,
    requirement_to_chunk,
)
from app.services.knowledge_chunk_service import KnowledgeChunkService

DEFAULT_DATASET = Path("eval/fixtures/phase6_simulated.json")
STRATEGIES = (
    "fixed-256",
    "fixed-512",
    "fixed-800",
    "fixed-1200",
    "semantic",
    "entity+heading",
)


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    kind: str
    max_chars: int | None = None
    overlap_chars: int = 0
    threshold: float | None = None
    min_chars: int | None = None


@dataclass
class StrategyResult:
    strategy: str
    chunk_count: int
    average_chars: float
    median_chars: float
    max_chars: int
    chunking_seconds: float
    index_seconds: float
    total_setup_seconds: float
    query_p95_seconds: float
    embedding_calls_during_chunking: int
    embedding_texts_during_chunking: int
    embedding_calls_during_index: int
    embedding_texts_during_index: int
    metrics: dict[str, float | int]


def _strategy_configs() -> list[StrategyConfig]:
    return [
        StrategyConfig("fixed-256", "fixed", max_chars=256, overlap_chars=64),
        StrategyConfig("fixed-512", "fixed", max_chars=512, overlap_chars=64),
        StrategyConfig("fixed-800", "fixed", max_chars=800, overlap_chars=64),
        StrategyConfig("fixed-1200", "fixed", max_chars=1200, overlap_chars=64),
        StrategyConfig(
            "semantic",
            "semantic",
            max_chars=800,
            threshold=0.55,
            min_chars=40,
        ),
        StrategyConfig("entity+heading", "entity"),
    ]


class _CountingEmbeddingClient:
    """委托给真实客户端，只统计调用次数与文本条数，不改变 Embedding 行为。"""

    def __init__(self, delegate: EmbeddingClient) -> None:
        self._delegate = delegate
        self.calls = 0
        self.texts = 0

    @property
    def dimension(self) -> int:
        return self._delegate.dimension

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        self.texts += len(texts)
        return self._delegate.embed_texts(texts)

    def embed_query(self, text: str) -> list[float]:
        self.calls += 1
        self.texts += 1
        return self._delegate.embed_query(text)


def build_engine(database_url: str, *, allow_sqlite: bool = False) -> Engine:
    """限制可写入的实验库，避免把隔离实验误跑到开发库。"""
    if database_url.startswith("sqlite"):
        if not allow_sqlite:
            raise ValueError("默认拒绝写入 SQLite；离线演练需显式 allow_sqlite=True")
        engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        return engine

    database_name = database_url.rsplit("/", 1)[-1].split("?", 1)[0]
    if "chunking" not in database_name:
        raise ValueError(
            "实验数据库名必须包含 chunking，防止误写开发库：" f"{database_name}"
        )
    return create_engine(database_url, pool_pre_ping=True)


def _clear_experiment_data(session: Session) -> None:
    session.execute(delete(KnowledgeChunk))
    session.execute(delete(TestCase))
    session.execute(delete(Defect))
    session.execute(delete(Requirement))
    session.execute(delete(Document))
    session.commit()


def _seed_entities(session: Session, dataset: ChunkingDataset) -> None:
    session.add_all(TestCase(**item.model_dump()) for item in dataset.testcases)
    session.add_all(Defect(**item.model_dump()) for item in dataset.defects)
    session.add_all(Requirement(**item.model_dump()) for item in dataset.requirements)
    for item in dataset.documents:
        session.add(
            Document(
                doc_id=item.doc_id,
                title=item.title,
                doc_type=item.doc_type,
                path=item.path,
                version=item.version,
                content_hash=content_sha256(item.content),
                meta={"section_count": len(MarkdownParser.parse(item.content).sections)},
            )
        )
    session.commit()


def _entity_chunks(session: Session) -> list[KnowledgeChunkCreate]:
    return [
        *(
            case_to_chunk(item)
            for item in session.scalars(select(TestCase).order_by(TestCase.case_id))
        ),
        *(
            defect_to_chunk(item)
            for item in session.scalars(select(Defect).order_by(Defect.bug_id))
        ),
        *(
            requirement_to_chunk(item)
            for item in session.scalars(select(Requirement).order_by(Requirement.req_id))
        ),
    ]


def _document_chunks(
    dataset: ChunkingDataset,
    config: StrategyConfig,
    *,
    embedding: EmbeddingClient,
) -> list[KnowledgeChunkCreate]:
    drafts_by_doc: dict[str, Sequence[ChunkDraft]] = {}
    for document in dataset.documents:
        if config.kind == "entity":
            # 文档侧“实体”实际是 Markdown 标题节点，因此策略统一命名
            # entity+heading，避免把它与结构化实体的一实体一 Chunk 混为一谈。
            parsed = MarkdownParser.parse(document.content, fallback_title=document.title)
            drafts_by_doc[document.doc_id] = [
                ChunkDraft(
                    content=section.content,
                    start=document.content.find(section.content),
                    end=document.content.find(section.content) + len(section.content),
                    heading_path=section.path,
                )
                for section in parsed.sections
            ]
        elif config.kind == "fixed":
            assert config.max_chars is not None
            drafts_by_doc[document.doc_id] = FixedChunker(
                max_chars=config.max_chars,
                overlap_chars=config.overlap_chars,
            ).split(document.content)
        elif config.kind == "semantic":
            assert config.max_chars is not None and config.threshold is not None
            drafts_by_doc[document.doc_id] = SemanticChunker(
                embedding_client=embedding,
                max_chars=config.max_chars,
                threshold=config.threshold,
                min_chars=config.min_chars or 40,
            ).split(document.content)
        else:  # pragma: no cover - 配置常量只包含上面三种
            raise ValueError(f"未知策略：{config.kind}")

    items: list[KnowledgeChunkCreate] = []
    documents_by_id = {item.doc_id: item for item in dataset.documents}
    for doc_id, drafts in drafts_by_doc.items():
        document = documents_by_id[doc_id]
        for index, draft in enumerate(drafts):
            if not draft.content.strip():
                continue
            items.append(
                KnowledgeChunkCreate(
                    source_type="document",
                    source_id=doc_id,
                    document_id=doc_id,
                    chunk_index=index,
                    content=draft.content,
                    metadata={
                        "title": document.title,
                        "strategy": config.name,
                        "span_start": draft.start,
                        "span_end": draft.end,
                        "heading_path": list(draft.heading_path),
                        "overlap_prefix": draft.overlap_prefix,
                    },
                    version=document.version,
                )
            )
    return items


def _all_chunks(
    session: Session,
    dataset: ChunkingDataset,
    config: StrategyConfig,
    *,
    embedding: EmbeddingClient,
) -> list[KnowledgeChunkCreate]:
    items = _entity_chunks(session)
    items.extend(_document_chunks(dataset, config, embedding=embedding))
    return items


def _index(
    session: Session,
    items: Sequence[KnowledgeChunkCreate],
    *,
    embedding: EmbeddingClient,
) -> list[KnowledgeChunk]:
    return KnowledgeChunkService.create_many(
        session,
        items=items,
        embedding_client=embedding,
        replace_source_types=["testcase", "defect", "requirement", "document"],
    )


def evaluate_strategy(
    session: Session,
    dataset: ChunkingDataset,
    config: StrategyConfig,
    *,
    embedding: EmbeddingClient,
    top_k: int = 10,
) -> StrategyResult:
    """清空、重建并评测一个策略；调用方负责库隔离。"""
    _clear_experiment_data(session)
    _seed_entities(session, dataset)

    counting_embedding = _CountingEmbeddingClient(embedding)
    chunking_started = time.perf_counter()
    items = _all_chunks(
        session,
        dataset,
        config,
        embedding=counting_embedding,
    )
    chunking_seconds = time.perf_counter() - chunking_started
    embedding_calls_during_chunking = counting_embedding.calls
    embedding_texts_during_chunking = counting_embedding.texts
    counting_embedding.calls = 0
    counting_embedding.texts = 0

    start = time.perf_counter()
    chunks = _index(session, items, embedding=counting_embedding)
    index_seconds = time.perf_counter() - start
    embedding_calls_during_index = counting_embedding.calls
    embedding_texts_during_index = counting_embedding.texts

    lengths = [len(chunk.content) for chunk in chunks]
    evaluator = RetrievalEvaluation(k_values=(5, 10))
    query_latencies: list[float] = []
    for query in dataset.queries:
        started = time.perf_counter()
        results = KnowledgeChunkService.search(
            session,
            query_embedding=embedding.embed_query(query.query),
            limit=top_k,
        )
        query_latencies.append(time.perf_counter() - started)
        evaluator.add(
            query,
            [chunk for chunk, _ in results],
            distances=[distance for _, distance in results],
        )

    return StrategyResult(
        strategy=config.name,
        chunk_count=len(chunks),
        average_chars=statistics.mean(lengths) if lengths else 0.0,
        median_chars=statistics.median(lengths) if lengths else 0.0,
        max_chars=max(lengths, default=0),
        chunking_seconds=chunking_seconds,
        index_seconds=index_seconds,
        total_setup_seconds=chunking_seconds + index_seconds,
        query_p95_seconds=percentile(query_latencies, 0.95),
        embedding_calls_during_chunking=embedding_calls_during_chunking,
        embedding_texts_during_chunking=embedding_texts_during_chunking,
        embedding_calls_during_index=embedding_calls_during_index,
        embedding_texts_during_index=embedding_texts_during_index,
        metrics=evaluator.summary(),
    )


def run_experiment(
    engine: Engine,
    dataset: ChunkingDataset,
    *,
    embedding: EmbeddingClient,
    strategies: Sequence[StrategyConfig] | None = None,
    top_k: int = 10,
) -> list[StrategyResult]:
    results: list[StrategyResult] = []
    with Session(engine, expire_on_commit=False) as session:
        for config in strategies or _strategy_configs():
            results.append(
                evaluate_strategy(
                    session,
                    dataset,
                    config,
                    embedding=embedding,
                    top_k=top_k,
                )
            )
    return results


def _manifest(
    dataset: ChunkingDataset,
    results: Sequence[StrategyResult],
    configs: Sequence[StrategyConfig],
    *,
    database_url: str,
    dataset_path: str | Path,
    started_at: datetime,
    finished_at: datetime,
    command: Sequence[str],
) -> dict[str, Any]:
    database_name = database_url.rsplit("/", 1)[-1].split("?", 1)[0]
    git_commit = "unknown"
    git_dirty = False
    try:
        git_commit = _git_output(["rev-parse", "HEAD"]) or "unknown"
        git_dirty = bool(_git_output(["status", "--porcelain"]))
    except (OSError, subprocess.CalledProcessError):
        git_commit = "unknown"

    return {
        "phase": 6,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "generated_at": finished_at.isoformat(),
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "command": _redact_command(command),
        "dataset": {
            "path": str(dataset_path),
            "sha256": dataset.dataset_sha256,
            "queries": len(dataset.queries),
            "answerable": sum(1 for query in dataset.queries if query.answerable),
            "unanswerable": sum(1 for query in dataset.queries if not query.answerable),
        },
        "environment": {
            "database_name": database_name,
            "embedding_base_url": settings.embedding_base_url,
            "embedding_model": settings.embedding_model,
            "embedding_dim": settings.embedding_dim,
        },
        "strategy_configs": [
            asdict(config)
            for config in configs
        ],
        "strategies": [asdict(result) for result in results],
    }


def _redact_command(command: Sequence[str]) -> list[str]:
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


def _git_output(arguments: Sequence[str]) -> str | None:
    output = subprocess.check_output(
        ["git", *arguments],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()
    return output or None


def _verify_experiment_database(engine: Engine) -> None:
    with engine.connect() as connection:
        dialect = connection.dialect.name
        if dialect != "postgresql":
            raise ValueError(f"真实实验只支持 PostgreSQL，当前 dialect={dialect}")
        vector = connection.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        ).scalar()
        if not vector:
            raise ValueError("目标数据库未安装 pgvector 扩展")


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 6 Chunking 对比实验")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument(
        "--database-url",
        default=f"{settings.database_url.rsplit('/', 1)[0]}/aitkp_chunking",
    )
    parser.add_argument("--output", default="results/chunking-experiment.json")
    parser.add_argument("--dry-run", action="store_true", help="只校验数据集与策略，不写库")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--semantic-threshold", type=float, default=0.55)
    parser.add_argument("--semantic-min-chars", type=int, default=40)
    args = parser.parse_args()
    if args.semantic_min_chars < 0:
        parser.error("--semantic-min-chars 必须 >= 0")
    if not 0 < args.semantic_threshold <= 1:
        parser.error("--semantic-threshold 必须在 (0, 1] 内")

    dataset = load_dataset(args.dataset)
    configs = [
        StrategyConfig(
            name=config.name,
            kind=config.kind,
            max_chars=config.max_chars,
            overlap_chars=config.overlap_chars,
            threshold=args.semantic_threshold,
            min_chars=args.semantic_min_chars,
        )
        if config.kind == "semantic"
        else config
        for config in _strategy_configs()
    ]
    if args.dry_run:
        print(
            f"dataset={args.dataset} sha256={dataset.dataset_sha256[:12]} "
            f"queries={len(dataset.queries)} strategies={','.join(item.name for item in configs)}"
        )
        for document in dataset.documents:
            print(
                f"{document.doc_id}: fixed256={len(FixedChunker(256, 64).split(document.content))} "
                f"entity_sections={len(MarkdownParser.parse(document.content).sections)}"
            )
        return

    started_at = datetime.now(UTC)
    engine = build_engine(args.database_url)
    _verify_experiment_database(engine)
    embedding = get_embedding_client()
    results = run_experiment(engine, dataset, embedding=embedding, top_k=args.top_k)
    manifest = _manifest(
        dataset,
        results,
        configs,
        database_url=args.database_url,
        dataset_path=args.dataset,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        command=[sys.executable, *sys.argv],
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
            f"{result.strategy:10s} chunks={result.chunk_count:3d} "
            f"avg={result.average_chars:6.1f} "
            f"R@5={metrics['recall@5']:.3f} R@10={metrics['recall@10']:.3f} "
            f"MRR={metrics['mrr']:.3f} "
            f"chunk={result.chunking_seconds:.1f}s "
            f"index={result.index_seconds:.1f}s"
        )
    print(f"manifest={output}")


if __name__ == "__main__":
    main()
