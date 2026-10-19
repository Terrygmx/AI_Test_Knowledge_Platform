"""Phase 10 Metadata Filter 实验的纯逻辑。

本模块不访问数据库。调用方提供同一批候选、Query Embedding 与 SQL 过滤器，
这里按固定顺序比较：

- ``none``：不过滤，直接取 Top-K；
- ``pre``：先用 Metadata 缩小候选，再做 Vector / BM25；
- ``post``：先取未过滤 Top-K，再过滤，可能发生候选饥饿；
- ``during``：当前作为 SQL 过滤后检索的执行别名；实验表未建 HNSW，
  因此本轮不把它宣称为独立的 ANN 过滤实验。

四个变体共享同一 Query 与候选，避免把过滤收益和 Embedding 变化混在一起。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.evaluation.dataset import ChunkingDataset, EvaluationQuery
from app.evaluation.lexical_experiment import k_values_for
from app.evaluation.metrics import RetrievalEvaluation, percentile
from app.retrieval.filters import normalize_metadata_filters
from app.retrieval.hybrid import ChannelHit, fuse
from app.retrieval.lexical import BM25Index, LexicalDocument


@dataclass(frozen=True)
class FilterCandidate:
    """实验候选；``meta`` 是过滤依据，``embedding`` 与 BM25 共用身份。"""

    source_type: str
    source_id: str
    chunk_index: int
    content: str
    embedding: tuple[float, ...]
    meta: Mapping[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.source_type, self.source_id, self.chunk_index)


@dataclass
class FilterVariantResult:
    name: str
    metrics: dict[str, float | int]
    query_p50_seconds: float
    query_p95_seconds: float
    candidate_counts_before: list[int] = field(default_factory=list)
    candidate_counts_after: list[int] = field(default_factory=list)
    result_counts: list[int] = field(default_factory=list)
    empty_result_queries: int = 0
    gold_filtered_out_queries: int = 0


@dataclass
class MetadataFilterExperimentResult:
    variants: list[FilterVariantResult]
    dataset_queries: int
    chunk_count: int


def matches_metadata(meta: Mapping[str, Any], filters: Mapping[str, Any] | None) -> bool:
    """Python 版过滤语义，必须与 SQL 实现保持一致。"""
    normalized = normalize_metadata_filters(filters)
    for field_name, expected in normalized.items():
        actual = meta.get(field_name)
        if field_name in {"tags", "related_cases"}:
            if not isinstance(actual, (list, tuple)):
                return False
            if not set(expected) & set(actual):
                return False
        elif actual != expected:
            return False
    return True


def _cosine_distance(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("向量维度不一致")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 1.0
    return 1.0 - dot / (left_norm * right_norm)


class _CandidateIndex:
    """在过滤后的候选上同时提供 Vector 与 BM25。"""

    def __init__(self, candidates: Sequence[FilterCandidate]) -> None:
        self.candidates = list(candidates)
        self.bm25 = BM25Index(
            [
                LexicalDocument(
                    source_type=candidate.source_type,
                    source_id=candidate.source_id,
                    content=candidate.content,
                )
                for candidate in self.candidates
            ]
        )

    def vector(self, embedding: Sequence[float], *, top_k: int) -> list[ChannelHit]:
        ranked = sorted(
            (
                (_cosine_distance(candidate.embedding, embedding), candidate)
                for candidate in self.candidates
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

    def keyword(self, query: str, *, top_k: int) -> list[ChannelHit]:
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
            for hit in self.bm25.search(query, limit=top_k)
        ]


class _MetricChunk:
    def __init__(self, hit: ChannelHit) -> None:
        self.source_type = hit.source_type
        self.source_id = hit.source_id
        self.content = hit.content


def _gold_is_kept(query: EvaluationQuery, candidates: Sequence[FilterCandidate]) -> bool:
    """过滤后的候选里是否仍可能召回至少一条 Gold 证据。"""
    for label in query.relevant:
        for candidate in candidates:
            if (
                candidate.source_type == label.source_type
                and candidate.source_id == label.source_id
                and label.evidence.casefold() in candidate.content.casefold()
            ):
                return True
    return not query.relevant


def _rank(
    candidates: Sequence[FilterCandidate],
    query: str,
    embedding: Sequence[float],
    *,
    top_k: int,
    candidate_k: int,
    strategy: str,
    rrf_k: int,
) -> list[ChannelHit]:
    index = _CandidateIndex(candidates)
    if strategy == "vector":
        return index.vector(embedding, top_k=top_k)
    if strategy == "keyword":
        return index.keyword(query, top_k=top_k)
    vector_hits = index.vector(embedding, top_k=candidate_k)
    keyword_hits = index.keyword(query, top_k=candidate_k)
    fused = fuse(
        vector_hits,
        keyword_hits,
        strategy="rrf",
        rrf_k=rrf_k,
        limit=top_k,
    )
    return [
        ChannelHit(
            source_type=item.source_type,
            source_id=item.source_id,
            chunk_index=item.chunk_index,
            score=item.score,
            content=item.content,
        )
        for item in fused
    ]


def evaluate_metadata_filters(
    dataset: ChunkingDataset,
    candidates: Sequence[FilterCandidate],
    query_embeddings: Sequence[Sequence[float]],
    *,
    top_k: int = 5,
    candidate_k: int = 20,
    strategy: str = "rrf",
    rrf_k: int = 60,
    prefilter: Callable[[Mapping[str, Any]], Sequence[FilterCandidate]] | None = None,
) -> MetadataFilterExperimentResult:
    """比较四个过滤变体；``prefilter`` 由调用方提供 SQL 结果或纯 Python 过滤。"""
    if len(query_embeddings) != len(dataset.queries):
        raise ValueError("query_embeddings 数量必须与 Query 数一致")
    if top_k < 1 or candidate_k < top_k:
        raise ValueError("必须满足 candidate_k >= top_k >= 1")
    if strategy not in {"vector", "keyword", "rrf"}:
        raise ValueError("strategy 必须是 vector、keyword 或 rrf")
    if rrf_k < 1:
        raise ValueError("rrf_k 必须 >= 1")

    variants = [
        ("none", "none"),
        ("pre", "pre"),
        ("post", "post"),
        ("during", "during"),
    ]
    k_values = k_values_for(top_k)
    results: list[FilterVariantResult] = []

    for name, mode in variants:
        evaluator = RetrievalEvaluation(k_values=k_values)
        latencies: list[float] = []
        before: list[int] = []
        after: list[int] = []
        result_counts: list[int] = []
        empty_queries = 0
        gold_filtered = 0

        for query, embedding in zip(dataset.queries, query_embeddings, strict=True):
            filters = normalize_metadata_filters(query.expected_filters)
            started = time.perf_counter()
            if mode == "none" or not filters:
                filtered = list(candidates)
                ranked = _rank(
                    filtered,
                    query.query,
                    embedding,
                    top_k=top_k,
                    candidate_k=candidate_k,
                    strategy=strategy,
                    rrf_k=rrf_k,
                )
            elif mode == "pre":
                filtered = (
                    list(prefilter(filters))
                    if prefilter is not None
                    else [
                        candidate
                        for candidate in candidates
                        if matches_metadata(candidate.meta, filters)
                    ]
                )
                ranked = _rank(
                    filtered,
                    query.query,
                    embedding,
                    top_k=top_k,
                    candidate_k=candidate_k,
                    strategy=strategy,
                    rrf_k=rrf_k,
                )
            elif mode == "post":
                # 先取未过滤 Top-K，再过滤：这是候选饥饿的复现路径。这里故意
                # 只对最终 Top-K 做过滤，不补取后续候选；补取会掩盖该路径的
                # 召回损失。`candidate_k` 只在 pre/during 的通道内使用。
                ranked_all = _rank(
                    candidates,
                    query.query,
                    embedding,
                    top_k=top_k,
                    candidate_k=candidate_k,
                    strategy=strategy,
                    rrf_k=rrf_k,
                )
                filtered = [
                    candidate
                    for candidate in candidates
                    if matches_metadata(candidate.meta, filters)
                ]
                allowed = {candidate.key for candidate in filtered}
                ranked = [
                    hit
                    for hit in ranked_all
                    if (hit.source_type, hit.source_id, hit.chunk_index) in allowed
                ][:top_k]
            else:
                # ``during`` 在纯逻辑实验中与 ``pre`` 同义；真实 SQL 运行由
                # 脚本把过滤谓词直接放进 pgvector 查询，单独记录执行路径。
                filtered = (
                    list(prefilter(filters))
                    if prefilter is not None
                    else [
                        candidate
                        for candidate in candidates
                        if matches_metadata(candidate.meta, filters)
                    ]
                )
                ranked = _rank(
                    filtered,
                    query.query,
                    embedding,
                    top_k=top_k,
                    candidate_k=candidate_k,
                    strategy=strategy,
                    rrf_k=rrf_k,
                )

            elapsed = time.perf_counter() - started
            latencies.append(elapsed)
            before.append(len(candidates))
            after.append(len(filtered))
            result_counts.append(len(ranked))
            if not ranked:
                empty_queries += 1
            if filters and not _gold_is_kept(query, filtered):
                gold_filtered += 1
            evaluator.add(query, [_MetricChunk(hit) for hit in ranked])

        results.append(
            FilterVariantResult(
                name=name,
                metrics=evaluator.summary(),
                query_p50_seconds=percentile(latencies, 0.5),
                query_p95_seconds=percentile(latencies, 0.95),
                candidate_counts_before=before,
                candidate_counts_after=after,
                result_counts=result_counts,
                empty_result_queries=empty_queries,
                gold_filtered_out_queries=gold_filtered,
            )
        )
    return MetadataFilterExperimentResult(
        variants=results,
        dataset_queries=len(dataset.queries),
        chunk_count=len(candidates),
    )


__all__ = [
    "FilterCandidate",
    "FilterVariantResult",
    "MetadataFilterExperimentResult",
    "evaluate_metadata_filters",
    "matches_metadata",
]
