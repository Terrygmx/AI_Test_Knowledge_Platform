"""Phase 3 最小 RAG：Vector Search → Context → LLM → Answer + Citation。"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.generation.context import assemble_context
from app.generation.llm import LLMClient, LLMError
from app.retrieval.embedding import EmbeddingClient
from app.schemas.rag import Citation, RAGResponse
from app.services.knowledge_chunk_service import KnowledgeChunkService

logger = get_logger(__name__)

INSUFFICIENT_EVIDENCE = "证据不足：当前知识库没有可用于回答该问题的内容。"
_CITATION_PATTERN = re.compile(r"\[\s*(\d+(?:\s*[,，]\s*\d+)*)\s*\]")


def build_prompt(question: str, context: str) -> str:
    """构造 grounding Prompt：证据与问题分区，禁止使用外部知识。"""
    return (
        "你是测试知识助手。只能根据下面的证据回答问题，不得补充证据之外的事实。\n"
        "每个结论后必须使用证据编号引用，例如 [1]；引用编号只能来自给出的证据。\n"
        "如果证据不足以回答，请明确写“证据不足”。\n\n"
        f"<evidence>\n{context}\n</evidence>\n\n"
        f"<question>\n{question.strip()}\n</question>\n"
    )


def extract_citations(answer: str, available: Sequence[Citation]) -> list[Citation]:
    """校验回答中的引用编号，并按首次出现顺序返回对应 Citation。"""
    numbers: list[int] = []
    for match in _CITATION_PATTERN.finditer(answer):
        for raw_number in re.split(r"\s*[,，]\s*", match.group(1)):
            number = int(raw_number)
            if number < 1 or number > len(available):
                raise LLMError(
                    "LLM 返回了不存在的引用编号",
                    detail={"citation": number, "available": len(available)},
                )
            if number not in numbers:
                numbers.append(number)

    if not numbers:
        return []
    return [available[number - 1] for number in numbers]


class RAGService:
    """手写最小 RAG Pipeline。"""

    @classmethod
    def answer_from_results(
        cls,
        *,
        question: str,
        results: Sequence[tuple[object, float]],
        llm: LLMClient,
        max_distance: float | None = None,
    ) -> RAGResponse:
        """从已检索结果生成回答，便于离线测试 Context / Citation。"""
        question = question.strip()
        if not question:
            raise LLMError("问题不能为空")
        eligible_results = list(results)
        if max_distance is not None:
            eligible_results = [
                (chunk, distance)
                for chunk, distance in results
                if distance <= max_distance
            ]
        bundle = assemble_context(eligible_results)  # type: ignore[arg-type]
        if not bundle.citations:
            return RAGResponse(
                question=question,
                answer=INSUFFICIENT_EVIDENCE,
                citation_valid=False,
                evidence_sufficient=False,
                citations=[],
                retrieved_count=0,
            )

        raw_answer = llm.generate(build_prompt(question, bundle.text))
        if not raw_answer.strip():
            raise LLMError("LLM 返回了空回答")
        if "证据不足" in raw_answer:
            return RAGResponse(
                question=question,
                answer=raw_answer.strip(),
                citation_valid=False,
                evidence_sufficient=False,
                citations=[],
                retrieved_count=len(bundle.citations),
            )
        citations = extract_citations(raw_answer, bundle.citations)
        return RAGResponse(
            question=question,
            answer=raw_answer.strip(),
            citation_valid=bool(citations),
            evidence_sufficient=(
                True if max_distance is not None and bool(citations) else None
            ),
            citations=citations,
            retrieved_count=len(bundle.citations),
        )

    @classmethod
    def answer(
        cls,
        session: Session,
        *,
        question: str,
        embedding_client: EmbeddingClient,
        llm: LLMClient | None = None,
        llm_factory: Callable[[], LLMClient] | None = None,
        top_k: int = 5,
        source_types: Sequence[str] | None = None,
        max_distance: float | None = None,
    ) -> RAGResponse:
        question = question.strip()
        if not question:
            raise LLMError("问题不能为空")
        if top_k < 1:
            raise LLMError("top_k 必须大于 0")

        results = KnowledgeChunkService.search(
            session,
            query_embedding=embedding_client.embed_query(question),
            limit=top_k,
            source_types=source_types,
        )
        logger.info(
            "RAG 检索 question=%r top_k=%s hits=%s distances=%s",
            question,
            top_k,
            len(results),
            [round(distance, 4) for _, distance in results],
        )
        if not results:
            return RAGResponse(
                question=question,
                answer=INSUFFICIENT_EVIDENCE,
                citation_valid=False,
                evidence_sufficient=False,
                citations=[],
                retrieved_count=0,
            )
        if llm is None:
            if llm_factory is None:
                raise LLMError("未提供 LLM 客户端")
            llm = llm_factory()
        return cls.answer_from_results(
            question=question,
            results=results,
            llm=llm,
            max_distance=max_distance,
        )


__all__ = ["INSUFFICIENT_EVIDENCE", "RAGService", "build_prompt", "extract_citations"]
