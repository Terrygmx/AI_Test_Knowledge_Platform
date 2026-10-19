"""Phase 3 最小 RAG：Vector Search → Context → LLM → Answer + Citation。"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from sqlalchemy.orm import Session

from app.generation.context import assemble_context
from app.generation.llm import LLMClient, LLMError
from app.retrieval.embedding import EmbeddingClient
from app.schemas.rag import Citation, RAGResponse
from app.services.knowledge_chunk_service import KnowledgeChunkService

INSUFFICIENT_EVIDENCE = "证据不足：当前知识库没有可用于回答该问题的内容。"
_CITATION_PATTERN = re.compile(r"\[(\d+)\]")


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
        number = int(match.group(1))
        if number < 1 or number > len(available):
            raise LLMError(
                "LLM 返回了不存在的引用编号",
                detail={"citation": number, "available": len(available)},
            )
        if number not in numbers:
            numbers.append(number)

    if not numbers:
        raise LLMError("LLM 回答缺少证据引用")
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
    ) -> RAGResponse:
        """从已检索结果生成回答，便于离线测试 Context / Citation。"""
        question = question.strip()
        if not question:
            raise LLMError("问题不能为空")
        bundle = assemble_context(results)  # type: ignore[arg-type]
        if not bundle.citations:
            return RAGResponse(
                question=question,
                answer=INSUFFICIENT_EVIDENCE,
                grounded=False,
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
                grounded=False,
                citations=[],
                retrieved_count=len(bundle.citations),
            )
        citations = extract_citations(raw_answer, bundle.citations)
        return RAGResponse(
            question=question,
            answer=raw_answer.strip(),
            grounded=True,
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
        if not results:
            return RAGResponse(
                question=question,
                answer=INSUFFICIENT_EVIDENCE,
                grounded=False,
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
        )


__all__ = ["INSUFFICIENT_EVIDENCE", "RAGService", "build_prompt", "extract_citations"]
