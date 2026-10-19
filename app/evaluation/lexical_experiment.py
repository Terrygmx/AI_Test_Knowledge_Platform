"""Phase 8 词法检索实验的纯逻辑。

实验把 Chunk 作为与 Phase 6 / 7 相同的检索单元，只替换排序算法：

- ``ILIKE`` 基线：模拟当前 ``UnifiedSearchService`` 的整串子串匹配；
- ``BM25``：应用层倒排索引 + BM25，能够对多关键词做稳定打分。

指标沿用 ``RetrievalEvaluation``，Gold Set 仍以来源 + 原文证据为锚点。
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.evaluation.dataset import ChunkingDataset, EvaluationQuery
from app.evaluation.metrics import RetrievalEvaluation, percentile
from app.ingestion.markdown import MarkdownParser
from app.models import Defect, Requirement, TestCase
from app.retrieval.lexical import BM25Index, LexicalDocument, tokenize
from app.services.index_service import (
    case_to_chunk,
    defect_to_chunk,
    requirement_to_chunk,
)


@dataclass(frozen=True)
class RetrievedChunk:
    """评测只需要来源、Chunk 身份与正文，不绑定 ORM 或实验表。"""

    source_type: str
    source_id: str
    content: str
    chunk_index: int = 0
    meta: dict[str, object] | None = None


@dataclass(frozen=True)
class LexicalResult:
    name: str
    top_k: int
    k_values: tuple[int, ...]
    metrics: dict[str, float | int]
    categories: dict[str, dict[str, float | int]]
    query_latencies: list[float]
    query_p50_seconds: float
    query_p95_seconds: float
    index_seconds: float
    evaluation_seconds: float


class LexicalSearcher(Protocol):
    name: str
    index_seconds: float

    def search(self, query: str, *, limit: int) -> list[RetrievedChunk]: ...


class ILikeSearcher:
    """当前 PostgreSQL / SQLite 关键词检索的等价基线。

    真实 SQL 查询直接对整段 Query 做 ``%query%``；这里为了不依赖数据库，
    使用相同的整串子串语义。它无法处理 ``P0 BUG-1234 login-api v1.3``
    这种包含多个独立词项的 Query。
    """

    name = "ilike"

    def __init__(self, documents: Sequence[RetrievedChunk]) -> None:
        self.documents = list(documents)
        self.index_seconds = 0.0

    def search(self, query: str, *, limit: int) -> list[RetrievedChunk]:
        if limit < 1:
            raise ValueError("limit 必须大于 0")
        needle = query.strip().casefold()
        if not needle:
            return []
        matches = [document for document in self.documents if needle in document.content.casefold()]
        return sorted(matches, key=lambda item: (item.source_type, item.source_id))[:limit]


class BM25Searcher:
    """BM25 的评测适配器，把命中还原为 ``RetrievedChunk``。"""

    name = "bm25"

    def __init__(self, documents: Sequence[RetrievedChunk], *, k1: float = 1.2, b: float = 0.75):
        started = time.perf_counter()
        self.documents = list(documents)
        self.index = BM25Index(
            [
                LexicalDocument(
                    source_type=document.source_type,
                    source_id=document.source_id,
                    content=document.content,
                )
                for document in documents
            ],
            k1=k1,
            b=b,
        )
        self.index_seconds = time.perf_counter() - started

    def search(self, query: str, *, limit: int) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(
                source_type=hit.document.source_type,
                source_id=hit.document.source_id,
                content=hit.document.content,
                chunk_index=self.documents[hit.document_index].chunk_index,
            )
            for hit in self.index.search(query, limit=limit)
        ]


class _MetricChunk:
    """满足 ``RetrievalEvaluation`` 所需的最小 Chunk 形状。"""

    def __init__(self, item: RetrievedChunk) -> None:
        self.source_type = item.source_type
        self.source_id = item.source_id
        self.content = item.content


def _evaluate_queries(
    queries: Sequence[EvaluationQuery],
    searcher: LexicalSearcher,
    *,
    top_k: int,
) -> tuple[dict[str, float | int], list[float]]:
    evaluator = RetrievalEvaluation(k_values=k_values_for(top_k))
    latencies: list[float] = []
    for query in queries:
        started = time.perf_counter()
        results = searcher.search(query.query, limit=top_k)
        latencies.append(time.perf_counter() - started)
        evaluator.add(query, [_MetricChunk(item) for item in results])
    return evaluator.summary(), latencies


def k_values_for(top_k: int) -> tuple[int, ...]:
    """返回不超过候选深度的标准 Recall K，并始终包含 ``top_k``。"""
    if top_k < 1:
        raise ValueError("top_k 必须大于 0")
    values = {top_k}
    values.update(k for k in (5, 10) if k <= top_k)
    return tuple(sorted(values))


def evaluate_searcher(
    searcher: LexicalSearcher,
    dataset: ChunkingDataset,
    *,
    name: str,
    top_k: int = 10,
) -> LexicalResult:
    """评测任意实现了 ``search`` 的检索器，并输出总指标与分类指标。"""
    if top_k < 1:
        raise ValueError("top_k 必须大于 0")
    metrics, latencies = _evaluate_queries(dataset.queries, searcher, top_k=top_k)

    categories: dict[str, dict[str, float | int]] = {}
    for category in sorted({query.category for query in dataset.queries}):
        category_metrics, _ = _evaluate_queries(
            [query for query in dataset.queries if query.category == category],
            searcher,
            top_k=top_k,
        )
        categories[category] = category_metrics

    return LexicalResult(
        name=name,
        top_k=top_k,
        k_values=k_values_for(top_k),
        metrics=metrics,
        categories=categories,
        query_latencies=latencies,
        query_p50_seconds=percentile(latencies, 0.5),
        query_p95_seconds=percentile(latencies, 0.95),
        index_seconds=searcher.index_seconds,
        evaluation_seconds=sum(latencies),
    )


def evaluate_lexical(
    dataset: ChunkingDataset,
    documents_or_index: BM25Index | Sequence[RetrievedChunk],
    *,
    top_k: int = 10,
) -> LexicalResult:
    """在 Phase 8 Gold Set 上评测 BM25，并输出分类指标。"""
    if top_k < 1:
        raise ValueError("top_k 必须大于 0")
    if isinstance(documents_or_index, BM25Index):
        return evaluate_searcher(
            _IndexedBM25Searcher(documents_or_index),
            dataset,
            name="bm25",
            top_k=top_k,
        )
    search_index = BM25Index(
        [
            LexicalDocument(
                source_type=item.source_type,
                source_id=item.source_id,
                content=item.content,
            )
            for item in documents_or_index
        ],
    )

    return evaluate_searcher(
        _IndexedBM25Searcher(search_index),
        dataset,
        name="bm25",
        top_k=top_k,
    )


class _IndexedBM25Searcher:
    """直接复用已建索引，避免评测延迟里混入建索引成本。"""

    name = "bm25"

    def __init__(self, index: BM25Index) -> None:
        self.index = index
        self.index_seconds = 0.0

    def search(self, query: str, *, limit: int) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(
                source_type=hit.document.source_type,
                source_id=hit.document.source_id,
                content=hit.document.content,
                chunk_index=getattr(
                    self.index.documents[hit.document_index],
                    "chunk_index",
                    0,
                ),
            )
            for hit in self.index.search(query, limit=limit)
        ]


def demonstrate_baseline_failure(
    documents: Sequence[RetrievedChunk],
    *,
    query: str = "P0 BUG-1234 login-api v1.3",
) -> dict[str, object]:
    """用一条组合关键词 Query 展示 ``ILIKE`` 与 BM25 的差异。"""
    ilike_results = ILikeSearcher(documents).search(query, limit=10)
    bm25_results = BM25Searcher(documents).search(query, limit=10)
    return {
        "query": query,
        "tokens": tokenize(query),
        "ilike": {
            "matches": len(ilike_results),
            "source_ids": [item.source_id for item in ilike_results],
        },
        "bm25": {
            "matches": len(bm25_results),
            "source_ids": [item.source_id for item in bm25_results],
            "top_source_id": bm25_results[0].source_id if bm25_results else None,
        },
    }


def retrieval_documents(chunks: Sequence[object]) -> list[RetrievedChunk]:
    """把实验 Chunk（含 ``source_type/source_id/content``）转为评测输入。"""
    return [
        RetrievedChunk(
            source_type=str(chunk.source_type),  # type: ignore[attr-defined]
            source_id=str(chunk.source_id),  # type: ignore[attr-defined]
            content=str(chunk.content),  # type: ignore[attr-defined]
            chunk_index=int(getattr(chunk, "chunk_index", 0)),
        )
        for chunk in chunks
    ]


def retrieve_chunks(dataset: ChunkingDataset) -> list[RetrievedChunk]:
    """无数据库重建 Phase 6 的 ``entity+heading`` 基线。

    结构化实体一实体一 Chunk；Markdown 文档一标题 Section 一 Chunk。正文和
    Metadata 的生成规则与 ``scripts.chunking_experiment._document_chunks`` 对齐，
    但这里不需要数据库或 Embedding，确保词法实验完全离线可复现。
    """
    chunks: list[RetrievedChunk] = []
    for seed in dataset.testcases:
        chunk = case_to_chunk(TestCase(**seed.model_dump()))
        chunks.append(
            RetrievedChunk(
                source_type=chunk.source_type,
                source_id=chunk.source_id,
                content=chunk.content,
                chunk_index=chunk.chunk_index,
                meta=dict(chunk.metadata),
            )
        )
    for seed in dataset.defects:
        chunk = defect_to_chunk(Defect(**seed.model_dump()))
        chunks.append(
            RetrievedChunk(
                source_type=chunk.source_type,
                source_id=chunk.source_id,
                content=chunk.content,
                chunk_index=chunk.chunk_index,
                meta=dict(chunk.metadata),
            )
        )
    for seed in dataset.requirements:
        chunk = requirement_to_chunk(Requirement(**seed.model_dump()))
        chunks.append(
            RetrievedChunk(
                source_type=chunk.source_type,
                source_id=chunk.source_id,
                content=chunk.content,
                chunk_index=chunk.chunk_index,
                meta=dict(chunk.metadata),
            )
        )
    for document in dataset.documents:
        parsed = MarkdownParser.parse(document.content, fallback_title=document.title)
        chunks.extend(
            RetrievedChunk(
                source_type="document",
                source_id=document.doc_id,
                content=section.content,
                chunk_index=index,
                meta={"doc_type": document.doc_type, "version": document.version},
            )
            for index, section in enumerate(parsed.sections)
        )
    return chunks


__all__ = [
    "BM25Searcher",
    "ILikeSearcher",
    "LexicalResult",
    "RetrievedChunk",
    "demonstrate_baseline_failure",
    "evaluate_lexical",
    "evaluate_searcher",
    "retrieve_chunks",
    "retrieval_documents",
]
