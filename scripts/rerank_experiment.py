#!/usr/bin/env python3
"""Phase 11 Rerank 实验。

默认使用 Phase 11 的 8 条排序精度 Query 与 Phase 8 的 47 个 Chunk，比较
Hybrid RRF 与 Hybrid + Rerank。真实运行只在库名包含 ``rerank`` 的
PostgreSQL 中创建隔离实验表。
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

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from app.core.config import settings
from app.evaluation.dataset import ChunkingDataset, load_dataset
from app.evaluation.hybrid_experiment import HybridCandidate, evaluate_hybrid
from app.evaluation.lexical_experiment import k_values_for, retrieve_chunks
from app.rerank.service import get_rerank_client
from app.retrieval.embedding import get_embedding_client

DEFAULT_DATASET = Path("eval/fixtures/phase11_simulated.json")
TABLE_NAME = "phase11_rerank_chunks"


def build_engine(database_url: str) -> Engine:
    """真实实验只写名称包含 rerank 的 PostgreSQL 库。"""
    database_name = database_url.rsplit("/", 1)[-1].split("?", 1)[0]
    if "rerank" not in database_name:
        raise ValueError(f"实验数据库名必须包含 rerank：{database_name}")
    return create_engine(database_url, pool_pre_ping=True)


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
            item = f"{scheme}://{credentials.split(':', 1)[0]}:***@{host}"
        redacted.append(item)
    return redacted


def _candidate_digest(dataset: ChunkingDataset, documents: list[Any]) -> str:
    payload = {
        "dataset": dataset.dataset_sha256,
        "chunks": [
            [item.source_type, item.source_id, item.chunk_index, item.content] for item in documents
        ],
    }
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _manifest(
    dataset: ChunkingDataset,
    result,
    *,
    dataset_path: str | Path,
    started_at: datetime,
    finished_at: datetime,
    command: list[str],
    result_path: str | Path,
    candidate_k: int,
    rerank_candidates: int,
    top_k: int,
    rrf_k: int,
    embedding_model: str,
    embedding_dimension: int,
    reranker_model: str,
    candidate_sha256: str,
    database_url: str,
) -> dict[str, Any]:
    database_name = database_url.rsplit("/", 1)[-1].split("?", 1)[0]
    return {
        "phase": 11,
        "manifest_schema_version": 1,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "git_commit": _git_output(["rev-parse", "HEAD"]) or "unknown",
        "git_commit_role": "generation",
        "git_dirty": bool(_git_output(["status", "--porcelain"])),
        "command": _redact_command(command),
        "result_path": str(result_path),
        "mode": "postgresql",
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
            "rerank_base_url": settings.rerank_base_url,
            "rerank_model": reranker_model,
        },
        "retrieval": {
            "strategy": "rrf",
            "candidate_k": candidate_k,
            "rerank_candidates": rerank_candidates,
            "top_k": top_k,
            "rrf_k": rrf_k,
            "ann_index": None,
            "latency_scope": (
                "vector SQL + candidate fetch + BM25 + fusion + rerank; "
                "excludes query embedding and index building"
            ),
            "rerank_boundary": (
                "Reranker may reorder but never add, remove, or rewrite candidates"
            ),
        },
        "index_seconds": result.index_seconds,
        "query_embedding_seconds": result.query_embedding_seconds,
        "rerank_seconds": result.rerank_seconds,
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
    parser = argparse.ArgumentParser(description="Phase 11 Hybrid + Rerank 实验")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument(
        "--database-url",
        default=f"{settings.database_url.rsplit('/', 1)[0]}/aitkp_rerank",
    )
    parser.add_argument("--output", default="results/rerank-phase11.json")
    parser.add_argument("--candidate-k", type=int, default=20)
    parser.add_argument("--rerank-candidates", type=int, default=20)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.top_k < 1 or args.candidate_k < args.top_k:
        parser.error("必须满足 --candidate-k >= --top-k >= 1")
    if args.rerank_candidates < args.top_k:
        parser.error("必须满足 --rerank-candidates >= --top-k >= 1")
    if args.rrf_k < 1:
        parser.error("--rrf-k 必须 >= 1")

    dataset = load_dataset(args.dataset)
    documents = retrieve_chunks(dataset)
    if args.dry_run:
        print(
            f"dataset={args.dataset} sha256={dataset.dataset_sha256[:12]} "
            f"queries={len(dataset.queries)} chunks={len(documents)} "
            f"candidate_k={args.candidate_k} rerank_candidates={args.rerank_candidates} "
            f"top_k={args.top_k} rerank_model={settings.rerank_model}"
        )
        return

    started_at = datetime.now(UTC)
    engine = build_engine(args.database_url)
    from scripts.hybrid_experiment import _create_vector_table, _verify_database

    _verify_database(engine)
    embedding = get_embedding_client()
    target_dimension = embedding.dimension
    table = _create_vector_table(engine, target_dimension)
    index_started = time.perf_counter()
    embeddings = embedding.embed_texts([item.content for item in documents])
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
    reranker = get_rerank_client()
    result = evaluate_hybrid(
        dataset,
        candidates,
        query_embeddings,
        top_k=args.top_k,
        candidate_k=args.candidate_k,
        fusion="rrf",
        rrf_k=args.rrf_k,
        engine=engine,
        vector_table=table,
        reranker=reranker,
        rerank_candidates=args.rerank_candidates,
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
        rerank_candidates=args.rerank_candidates,
        top_k=args.top_k,
        rrf_k=args.rrf_k,
        embedding_model=settings.embedding_model,
        embedding_dimension=target_dimension,
        reranker_model=settings.rerank_model,
        candidate_sha256=_candidate_digest(dataset, documents),
        database_url=args.database_url,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    for channel in result.channels:
        metrics = channel.metrics
        recall = " ".join(f"R@{k}={metrics[f'recall@{k}']:.3f}" for k in k_values_for(args.top_k))
        print(
            f"{channel.name:12s} {recall} MRR={metrics['mrr']:.3f} "
            f"p95={channel.query_p95_seconds:.4f}s"
        )
    print(f"manifest={output}")


if __name__ == "__main__":
    main()
