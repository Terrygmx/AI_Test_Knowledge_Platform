"""Phase 18 完整 RAG Pipeline：Understanding → Filter → Hybrid → Rerank → LLM。

Pipeline 只组合既有检索 / 生成接缝，并输出逐阶段 trace。每个阶段的候选身份
与分数都保留原始值，供 Phase 19 Badcase 归因使用；trace 不是业务日志，
不包含用户敏感数据。
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.evaluation.metadata_filter_experiment import matches_metadata
from app.generation.context import assemble_context
from app.generation.llm import LLMClient
from app.generation.rag import INSUFFICIENT_EVIDENCE, UNCITED_ANSWER, extract_citations
from app.models import KnowledgeChunk
from app.query.rewrite import QueryRewriter
from app.query.understanding import QueryUnderstandingService
from app.rerank.hybrid import rerank_fused_hits
from app.rerank.service import RerankClient
from app.retrieval.embedding import EmbeddingClient
from app.retrieval.hybrid import ChannelHit, fuse
from app.retrieval.lexical import BM25Index, LexicalDocument
from app.schemas.rag import RAGResponse
from app.services.knowledge_chunk_service import KnowledgeChunkService

logger = get_logger(__name__)


@dataclass
class PipelineTrace:
    """一次完整 RAG 请求的逐阶段记录。"""

    original_query: str = ""
    understanding: dict[str, Any] = field(default_factory=dict)
    filters: dict[str, Any] = field(default_factory=dict)
    vector_hits: list[dict[str, Any]] = field(default_factory=list)
    keyword_hits: list[dict[str, Any]] = field(default_factory=list)
    fusion_hits: list[dict[str, Any]] = field(default_factory=list)
    rerank_hits: list[dict[str, Any]] = field(default_factory=list)
    final_context: list[dict[str, Any]] = field(default_factory=list)
    stage_seconds: dict[str, float] = field(default_factory=dict)
    fallback_reason: str | None = None


def _hit_records(items: Sequence[Any]) -> list[dict[str, Any]]:
    """把通道候选 / 融合候选转成可 JSON 化的紧凑记录。"""
    records: list[dict[str, Any]] = []
    for rank, item in enumerate(items, start=1):
        record: dict[str, Any] = {
            "rank": rank,
            "key": getattr(item, "key_str", None) and item.key_str()
            or f"{item.source_type}:{item.source_id}:{item.chunk_index}",
            "score": item.score,
        }
        for name in ("vector_rank", "keyword_rank", "vector_score", "keyword_score"):
            value = getattr(item, name, None)
            if value is not None:
                record[name] = value
        records.append(record)
    return records


class RAGPipeline:
    """组合 Query Understanding、Hybrid 检索、Rerank 与生成。"""

    def __init__(
        self,
        *,
        understanding: QueryUnderstandingService,
        rewriter: QueryRewriter,
        embedding_client: EmbeddingClient,
        llm: LLMClient,
        reranker: RerankClient | None = None,
        rerank_top_n: int | None = None,
        fusion_strategy: str = "rrf",
        vector_weight: float = 1.0,
        keyword_weight: float = 1.0,
    ) -> None:
        self._understanding = understanding
        self._rewriter = rewriter
        self._embedding = embedding_client
        self._llm = llm
        self._reranker = reranker
        self._rerank_top_n = rerank_top_n
        self._fusion_strategy = fusion_strategy
        self._vector_weight = vector_weight
        self._keyword_weight = keyword_weight

    def answer(
        self,
        session: Session,
        *,
        question: str,
        top_k: int = 5,
        max_distance: float | None = None,
    ) -> RAGResponse:
        if not question.strip():
            raise ValueError("question 不能为空白")
        if top_k < 1:
            raise ValueError("top_k 必须大于 0")
        trace = PipelineTrace(original_query=question.strip())
        response = self._answer(
            session,
            question=question,
            top_k=top_k,
            trace=trace,
            max_distance=max_distance,
        )
        response.trace = trace.__dict__
        logger.info(
            "RAG pipeline 完成 question=%r stages=%s answer_cited=%s",
            question,
            list(trace.stage_seconds),
            response.citation_valid,
        )
        return response

    def _answer(
        self,
        session: Session,
        *,
        question: str,
        top_k: int,
        trace: PipelineTrace,
        max_distance: float | None,
    ) -> RAGResponse:
        stage_started = time.perf_counter()
        understanding = self._understanding.understand(question)
        rewrite_result = self._rewriter.rewrite(question)
        trace.understanding = understanding.model_dump(mode="json")
        trace.filters = understanding.to_metadata_filters()
        trace.fallback_reason = rewrite_result.fallback_reason
        trace.stage_seconds["understanding"] = time.perf_counter() - stage_started

        search_query = rewrite_result.search_query
        stage_started = time.perf_counter()
        query_embedding = self._embedding.embed_query(search_query)
        trace.stage_seconds["query_embedding"] = time.perf_counter() - stage_started

        # Pipeline 的候选深度固定为 final top_k 的两倍，先保证 Hybrid / Rerank
        # 有可重排空间；正式参数扫描仍由 Phase 9 / 11 实验负责。
        candidate_k = max(top_k * 2, 10)
        all_chunks = [
            chunk
            for chunk in session.query(KnowledgeChunk).all()
            if _matches_filters(chunk, trace.filters)
        ]
        vector_results = _offline_vector_results(all_chunks, query_embedding, candidate_k)
        keyword_results = _offline_keyword_results(all_chunks, search_query, candidate_k)
        trace.vector_hits = [
            {"rank": rank, "key": _chunk_key(chunk), "score": distance}
            for rank, (chunk, distance) in enumerate(vector_results, start=1)
        ]
        stage_started = time.perf_counter()
        fused = fuse(
            [
                ChannelHit(
                    source_type=chunk.source_type,
                    source_id=chunk.source_id,
                    chunk_index=chunk.chunk_index,
                    score=distance,
                    content=chunk.content,
                    higher_is_better=False,
                )
                for chunk, distance in vector_results
            ],
            [
                ChannelHit(
                    source_type=chunk.source_type,
                    source_id=chunk.source_id,
                    chunk_index=chunk.chunk_index,
                    score=score,
                    content=chunk.content,
                    higher_is_better=True,
                )
                for chunk, score, _terms in keyword_results
            ],
            strategy=self._fusion_strategy,
            vector_weight=self._vector_weight,
            keyword_weight=self._keyword_weight,
            limit=candidate_k,
        )
        trace.keyword_hits = [
            {"rank": rank, "key": _chunk_key(chunk), "score": score}
            for rank, (chunk, score, _terms) in enumerate(keyword_results, start=1)
        ]
        trace.fusion_hits = _hit_records(fused)
        trace.stage_seconds["hybrid"] = time.perf_counter() - stage_started
        if not fused:
            return _insufficient_response(question, trace)

        stage_started = time.perf_counter()
        ranked = fused
        if self._reranker is not None:
            ranked = rerank_fused_hits(
                fused,
                search_query,
                self._reranker,
                top_n=self._rerank_top_n or candidate_k,
            )
        trace.rerank_hits = _hit_records(ranked)
        trace.stage_seconds["rerank"] = time.perf_counter() - stage_started

        stage_started = time.perf_counter()
        chunks_by_key = KnowledgeChunkService.get_by_keys(session, [hit.key for hit in ranked])
        results: list[tuple[KnowledgeChunk, float]] = []
        for hit in ranked:
            chunk = chunks_by_key.get(hit.key)
            if chunk is not None:
                results.append((chunk, hit.score))
        trace.stage_seconds["evidence_load"] = time.perf_counter() - stage_started
        if not results:
            return _insufficient_response(question, trace)

        stage_started = time.perf_counter()
        bundle = assemble_context(results)
        trace.final_context = [
            {
                "rank": citation.rank,
                "key": f"{citation.source_type}:{citation.source_id}:{citation.chunk_index}",
                "score": citation.score,
            }
            for citation in bundle.citations
        ]
        if max_distance is not None:
            bundle = _filter_context(bundle, max_distance)
            trace.final_context = [
                {
                    "rank": citation.rank,
                    "key": f"{citation.source_type}:{citation.source_id}:{citation.chunk_index}",
                    "score": citation.score,
                }
                for citation in bundle.citations
            ]
        trace.stage_seconds["context"] = time.perf_counter() - stage_started
        if not bundle.citations:
            return _insufficient_response(question, trace, retrieved=len(ranked))

        stage_started = time.perf_counter()
        from app.generation.rag import build_prompt

        raw_answer = self._llm.generate(build_prompt(question.strip(), bundle.text))
        trace.stage_seconds["llm"] = time.perf_counter() - stage_started
        if not raw_answer.strip():
            from app.generation.llm import LLMError

            raise LLMError("LLM 返回了空回答")
        citations = extract_citations(raw_answer, bundle.citations)
        if not citations:
            answer = raw_answer.strip() if "证据不足" in raw_answer else UNCITED_ANSWER
            return RAGResponse(
                question=question.strip(),
                answer=answer,
                citation_valid=False,
                evidence_sufficient=True,
                citations=[],
                retrieved_count=len(bundle.citations),
            )
        return RAGResponse(
            question=question.strip(),
            answer=raw_answer.strip(),
            citation_valid=True,
            evidence_sufficient=True,
            citations=citations,
            retrieved_count=len(bundle.citations),
        )


def _chunk_key(chunk: KnowledgeChunk) -> str:
    return f"{chunk.source_type}:{chunk.source_id}:{chunk.chunk_index}"


def _matches_filters(chunk: KnowledgeChunk, filters: dict[str, Any]) -> bool:
    return matches_metadata(chunk.meta or {}, filters or None)


def _offline_vector_results(
    chunks: Sequence[KnowledgeChunk],
    query_embedding: Sequence[float],
    limit: int,
) -> list[tuple[KnowledgeChunk, float]]:
    """精确 cosine distance 排序，只用于离线 / SQLite 路径。"""
    def distance(chunk: KnowledgeChunk) -> float:
        vector = chunk.embedding or []
        dot = sum(a * b for a, b in zip(query_embedding, vector, strict=False))
        left_norm = sum(value * value for value in query_embedding) ** 0.5
        right_norm = sum(value * value for value in vector) ** 0.5
        if left_norm == 0 or right_norm == 0:
            return 1.0
        return 1.0 - dot / (left_norm * right_norm)

    with_vector = [chunk for chunk in chunks if chunk.embedding]
    ranked = sorted(with_vector, key=distance)
    return [(chunk, distance(chunk)) for chunk in ranked[:limit]]


def _offline_keyword_results(
    chunks: Sequence[KnowledgeChunk],
    query: str,
    limit: int,
) -> list[tuple[KnowledgeChunk, float, list[str]]]:
    documents = [
        LexicalDocument(
            source_type=chunk.source_type,
            source_id=chunk.source_id,
            content=chunk.content,
        )
        for chunk in chunks
    ]
    chunk_by_document = {
        index: chunk for index, chunk in enumerate(chunks)
    }
    index = BM25Index(
        documents
    )
    hits = index.search(query, limit=limit)
    results = []
    for hit in hits:
        chunk = chunk_by_document.get(hit.document_index)
        if chunk is not None:
            results.append((chunk, hit.score, hit.matched_terms))
    return results


def _insufficient_response(
    question: str,
    trace: PipelineTrace,
    *,
    retrieved: int = 0,
) -> RAGResponse:
    return RAGResponse(
        question=question.strip(),
        answer=INSUFFICIENT_EVIDENCE,
        citation_valid=False,
        evidence_sufficient=False,
        citations=[],
        retrieved_count=retrieved,
    )


def _filter_context(bundle: Any, max_distance: float) -> Any:
    from app.generation.context import ContextBundle

    kept = [c for c in bundle.citations if c.score is None or c.score <= max_distance]
    return ContextBundle(text=bundle.text, citations=kept)
