"""Phase 9 Hybrid Search 实验的纯逻辑。

实验固定同一份 Chunk 与 Gold Set，分别计算：

- Vector：pgvector cosine distance Top-K；
- Keyword：Phase 8 BM25 Top-K；
- Weighted：每个通道内部 min-max 归一化后加权；
- RRF：按通道名次融合。

本模块不写数据库；调用方负责使用隔离的 PostgreSQL 实验库。
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import Table, select
from sqlalchemy.engine import Engine

from app.evaluation.dataset import ChunkingDataset
from app.evaluation.metrics import RetrievalEvaluation, percentile
from app.models.types import POSTGRES
from app.retrieval.hybrid import ChannelHit, FusedHit, fuse
from app.retrieval.lexical import BM25Index, LexicalDocument


@dataclass(frozen=True)
class HybridCandidate:
    """一个实验 Chunk；向量与 BM25 共用同一身份。"""

    source_type: str
    source_id: str
    chunk_index: int
    content: str
    embedding: tuple[float, ...]

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.source_type, self.source_id, self.chunk_index)


@dataclass
class HybridChannelResult:
    """单次融合的结果与分段延迟。"""

    name: str
    strategy: str
    metrics: dict[str, float | int]
    query_latencies: list[float] = field(default_factory=list)
    query_p50_seconds: float = 0.0
    query_p95_seconds: float = 0.0
    vector_candidate_hits: int = 0
    keyword_candidate_hits: int = 0
    fused_candidates: int = 0


@dataclass
class HybridExperimentResult:
    channels: list[HybridChannelResult]
    dataset_queries: int
    chunk_count: int
    index_seconds: float = 0.0
    query_embedding_seconds: float = 0.0


def _cosine_distance(left: Sequence[float], right: Sequence[float]) -> float:
    """计算 cosine distance；单位向量与 pgvector 的 ``<=>`` 同义。"""
    if len(left) != len(right):
        raise ValueError("向量维度不一致")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 1.0
    return 1.0 - dot / (left_norm * right_norm)


def _vector_hits(
    candidates: Sequence[HybridCandidate],
    query_embedding: Sequence[float],
    *,
    top_k: int,
) -> list[ChannelHit]:
    ranked = sorted(
        (
            (
                _cosine_distance(candidate.embedding, query_embedding),
                candidate,
            )
            for candidate in candidates
        ),
        key=lambda item: (
            item[0],
            item[1].source_type,
            item[1].source_id,
            item[1].chunk_index,
        ),
    )
    return [
        ChannelHit(
            source_type=candidate.source_type,
            source_id=candidate.source_id,
            chunk_index=candidate.chunk_index,
            score=distance,
            content=candidate.content,
            higher_is_better=False,
        )
        for distance, candidate in ranked[:top_k]
    ]


def vector_hits_from_engine(
    engine: Engine,
    table: Table,
    query_embedding: Sequence[float],
    *,
    top_k: int,
) -> list[ChannelHit]:
    """通过 pgvector 的 cosine distance 获取向量候选。"""
    if engine.dialect.name != POSTGRES:
        raise ValueError("真实 Hybrid 实验只支持 PostgreSQL + pgvector")
    distance = table.c.embedding.cosine_distance(list(query_embedding)).label("distance")
    with engine.connect() as connection:
        rows = connection.execute(
            select(
                table.c.source_type,
                table.c.source_id,
                table.c.chunk_index,
                table.c.content,
                distance,
            )
            .order_by(distance, table.c.source_type, table.c.source_id, table.c.chunk_index)
            .limit(top_k)
        ).all()
    return [
        ChannelHit(
            source_type=row.source_type,
            source_id=row.source_id,
            chunk_index=row.chunk_index,
            score=float(row.distance),
            content=row.content,
            higher_is_better=False,
        )
        for row in rows
    ]


class _BM25Adapter:
    """让 BM25 与融合器共享 Candidate -> ChannelHit 契约。"""

    def __init__(self, candidates: Sequence[HybridCandidate]) -> None:
        self.candidates = list(candidates)
        self.index = BM25Index(
            [
                LexicalDocument(
                    source_type=candidate.source_type,
                    source_id=candidate.source_id,
                    content=candidate.content,
                )
                for candidate in self.candidates
            ]
        )

    def search(self, query: str, *, top_k: int) -> list[ChannelHit]:
        return [
            ChannelHit(
                source_type=hit.document.source_type,
                source_id=hit.document.source_id,
                chunk_index=self.candidates[hit.document_index].chunk_index,
                score=hit.score,
                content=hit.document.content,
                higher_is_better=True,
                matched_terms=tuple(hit.matched_terms),
            )
            for hit in self.index.search(query, limit=top_k)
        ]


def _metric_chunk(hit: ChannelHit | FusedHit):
    class _MetricChunk:
        source_type = hit.source_type
        source_id = hit.source_id
        content = hit.content

    return _MetricChunk()


def evaluate_hybrid(
    dataset: ChunkingDataset,
    candidates: Sequence[HybridCandidate],
    query_embeddings: Sequence[Sequence[float]],
    *,
    top_k: int = 10,
    candidate_k: int = 20,
    fusion: str = "rrf",
    vector_weight: float = 1.0,
    keyword_weight: float = 1.0,
    rrf_k: int = 60,
    engine: Engine | None = None,
    vector_table: Table | None = None,
    index_seconds: float = 0.0,
    query_embedding_seconds: float = 0.0,
    variants: Sequence[tuple[str, str]] | None = None,
) -> HybridExperimentResult:
    """在同一批候选上评测 Vector / Keyword / Weighted / RRF。"""
    if len(query_embeddings) != len(dataset.queries):
        raise ValueError("query_embeddings 数量必须与 Query 数一致")
    if top_k < 1 or candidate_k < 1:
        raise ValueError("top_k 与 candidate_k 必须 >= 1")
    if candidate_k < top_k:
        raise ValueError("candidate_k 不能小于 top_k")

    bm25 = _BM25Adapter(candidates)
    if (engine is None) != (vector_table is None):
        raise ValueError("engine 与 vector_table 必须同时提供")
    variants = list(
        variants
        or [
            ("vector", "vector"),
            ("keyword", "keyword"),
            ("weighted", "weighted"),
            ("rrf", "rrf"),
        ]
    )
    results: list[HybridChannelResult] = []

    for name, strategy in variants:
        k_values = tuple(k for k in (5, 10) if k <= top_k) or (top_k,)
        evaluator = RetrievalEvaluation(k_values=k_values)
        latencies: list[float] = []
        vector_count = 0
        keyword_count = 0
        fused_count = 0
        for query, query_embedding in zip(dataset.queries, query_embeddings, strict=True):
            started = time.perf_counter()
            vector_hits: list[ChannelHit] = []
            keyword_hits: list[ChannelHit] = []
            if strategy in {"vector", "weighted", "rrf"}:
                if engine is not None and vector_table is not None:
                    vector_hits = vector_hits_from_engine(
                        engine,
                        vector_table,
                        query_embedding,
                        top_k=candidate_k,
                    )
                else:
                    vector_hits = _vector_hits(candidates, query_embedding, top_k=candidate_k)
                vector_count += len(vector_hits)
            if strategy in {"keyword", "weighted", "rrf"}:
                keyword_hits = bm25.search(query.query, top_k=candidate_k)
                keyword_count += len(keyword_hits)

            if strategy == "vector":
                chosen = vector_hits[:top_k]
                metric_hits = [_metric_chunk(hit) for hit in chosen]
            elif strategy == "keyword":
                chosen = keyword_hits[:top_k]
                metric_hits = [_metric_chunk(hit) for hit in chosen]
            else:
                chosen = fuse(
                    vector_hits,
                    keyword_hits,
                    strategy=strategy,
                    vector_weight=vector_weight,
                    keyword_weight=keyword_weight,
                    rrf_k=rrf_k,
                    limit=top_k,
                )
                metric_hits = [_metric_chunk(hit) for hit in chosen]
            latencies.append(time.perf_counter() - started)
            fused_count += len(chosen)
            evaluator.add(
                query,
                metric_hits,
                distances=[
                    item.score for item in chosen
                ]
                if strategy == "vector"
                else None,
            )

        results.append(
            HybridChannelResult(
                name=name,
                strategy=strategy,
                metrics=evaluator.summary(),
                query_latencies=latencies,
                query_p50_seconds=percentile(latencies, 0.5),
                query_p95_seconds=percentile(latencies, 0.95),
                vector_candidate_hits=vector_count,
                keyword_candidate_hits=keyword_count,
                fused_candidates=fused_count,
            )
        )
    return HybridExperimentResult(
        channels=results,
        dataset_queries=len(dataset.queries),
        chunk_count=len(candidates),
        index_seconds=index_seconds,
        query_embedding_seconds=query_embedding_seconds,
    )


__all__ = [
    "HybridCandidate",
    "HybridChannelResult",
    "HybridExperimentResult",
    "evaluate_hybrid",
    "vector_hits_from_engine",
]
