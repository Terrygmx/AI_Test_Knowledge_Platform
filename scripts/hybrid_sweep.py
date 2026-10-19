#!/usr/bin/env python3
"""Phase 9 融合参数敏感性实验。

复用一次 Chunk Embedding 与一次 Query Embedding，比较：
- Weighted 的 vector/keyword 权重；
- RRF 的 k 值。

真实运行只允许名称包含 ``hybrid`` 的 PostgreSQL 数据库。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.evaluation.dataset import load_dataset
from app.evaluation.hybrid_experiment import (
    HybridCandidate,
    evaluate_hybrid,
)
from app.evaluation.lexical_experiment import retrieve_chunks
from app.retrieval.embedding import get_embedding_client
from scripts.hybrid_experiment import (
    DEFAULT_DATASET,
    _candidate_digest,
    _create_vector_table,
    _fetch_model_metadata,
    _git_output,
    _redact_command,
    _verify_database,
    build_engine,
)


def parse_weights(value: str) -> list[tuple[float, float]]:
    pairs: list[tuple[float, float]] = []
    for raw in value.split(","):
        item = raw.strip()
        if not item:
            continue
        parts = item.split(":")
        if len(parts) != 2:
            raise ValueError(f"权重格式应为 vector:keyword：{item}")
        vector_weight, keyword_weight = (float(part) for part in parts)
        if vector_weight < 0 or keyword_weight < 0:
            raise ValueError(f"权重不能为负数：{item}")
        if vector_weight + keyword_weight <= 0:
            raise ValueError(f"权重之和必须大于 0：{item}")
        pairs.append((vector_weight, keyword_weight))
    if not pairs:
        raise ValueError("至少需要一个权重组合")
    return pairs


def parse_ints(value: str) -> list[int]:
    values = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not values or any(item < 1 for item in values):
        raise ValueError("k 值必须是正整数列表")
    return values


def _manifest(
    dataset,
    results: list[dict[str, Any]],
    **metadata: Any,
) -> dict[str, Any]:
    return {
        "phase": 9,
        "kind": "fusion-sensitivity",
        **metadata,
        "dataset": {
            "path": str(metadata["dataset_path"]),
            "sha256": dataset.dataset_sha256,
            "queries": len(dataset.queries),
        },
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 9 融合参数敏感性实验")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument(
        "--database-url",
        default=f"{settings.database_url.rsplit('/', 1)[0]}/aitkp_hybrid",
    )
    parser.add_argument("--output", default="results/hybrid-sweep-phase9.json")
    parser.add_argument("--candidate-k", type=int, default=20)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--weights", default="1:1,2:1,1:2,3:1,1:3")
    parser.add_argument("--rrf-k-values", default="10,20,40,60,100")
    args = parser.parse_args()

    if args.top_k < 1 or args.candidate_k < args.top_k:
        parser.error("必须满足 --candidate-k >= --top-k >= 1")
    weights = parse_weights(args.weights)
    rrf_values = parse_ints(args.rrf_k_values)

    dataset = load_dataset(args.dataset)
    documents = retrieve_chunks(dataset)
    started_at = datetime.now(UTC)
    engine = build_engine(args.database_url)
    _verify_database(engine)
    embedding = get_embedding_client()
    table = _create_vector_table(engine, embedding.dimension)

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
    query_started = time.perf_counter()
    query_embeddings = [embedding.embed_query(query.query) for query in dataset.queries]
    query_embedding_seconds = time.perf_counter() - query_started
    model_metadata = _fetch_model_metadata(
        base_url=settings.embedding_base_url,
        api_key=settings.embedding_api_key,
        model_names=[settings.embedding_model],
        timeout=settings.embedding_timeout,
    )
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

    results: list[dict[str, Any]] = []
    for vector_weight, keyword_weight in weights:
        evaluated = evaluate_hybrid(
            dataset,
            candidates,
            query_embeddings,
            top_k=args.top_k,
            candidate_k=args.candidate_k,
            fusion="weighted",
            vector_weight=vector_weight,
            keyword_weight=keyword_weight,
            engine=engine,
            vector_table=table,
            index_seconds=index_seconds,
            query_embedding_seconds=query_embedding_seconds,
            variants=[
                ("weighted", "weighted"),
                ("rrf", "rrf"),
            ],
        )
        for channel in evaluated.channels:
            results.append(
                {
                    "strategy": channel.strategy,
                    "vector_weight": vector_weight,
                    "keyword_weight": keyword_weight,
                    "rrf_k": 60,
                    "metrics": channel.metrics,
                    "query_p95_seconds": channel.query_p95_seconds,
                }
            )
    for rrf_k in rrf_values:
        evaluated = evaluate_hybrid(
            dataset,
            candidates,
            query_embeddings,
            top_k=args.top_k,
            candidate_k=args.candidate_k,
            fusion="rrf",
            rrf_k=rrf_k,
            engine=engine,
            vector_table=table,
            index_seconds=index_seconds,
            query_embedding_seconds=query_embedding_seconds,
            variants=[("rrf", "rrf")],
        )
        channel = evaluated.channels[0]
        results.append(
            {
                "strategy": channel.strategy,
                "vector_weight": 1.0,
                "keyword_weight": 1.0,
                "rrf_k": rrf_k,
                "metrics": channel.metrics,
                "query_p95_seconds": channel.query_p95_seconds,
            }
        )

    manifest = _manifest(
        dataset,
        results,
        started_at=started_at.isoformat(),
        finished_at=datetime.now(UTC).isoformat(),
        git_commit=_git_output(["rev-parse", "HEAD"]) or "unknown",
        git_dirty=bool(_git_output(["status", "--porcelain"])),
        command=_redact_command([sys.executable, *sys.argv]),
        dataset_path=args.dataset,
        database_name=args.database_url.rsplit("/", 1)[-1].split("?", 1)[0],
        chunks={"count": len(documents), "sha256": _candidate_digest(dataset, documents)},
        environment={
            "embedding_base_url": settings.embedding_base_url,
            "embedding_model": settings.embedding_model,
            "embedding_dimension": embedding.dimension,
            "embedding_batch_size": settings.embedding_batch_size,
            "embedding_timeout_seconds": settings.embedding_timeout,
            "embedding_max_retries": settings.embedding_max_retries,
        },
        embedding_service={"model_metadata": model_metadata},
        retrieval={
            "candidate_k": args.candidate_k,
            "top_k": args.top_k,
            "weights": weights,
            "rrf_k_values": rrf_values,
        },
        index_seconds=index_seconds,
        query_embedding_seconds=query_embedding_seconds,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for item in results:
        metrics = item["metrics"]
        recall = " ".join(
            f"R@{k}={metrics[f'recall@{k}']:.3f}"
            for k in (5, 10)
            if k <= args.top_k
        )
        print(
            f"{item['strategy']:8s} w={item['vector_weight']}:{item['keyword_weight']} "
            f"rrf_k={item['rrf_k']:3d} "
            f"{recall} MRR={metrics['mrr']:.3f}"
        )
    print(f"manifest={output}")


if __name__ == "__main__":
    main()
