"""Phase 3 最小 RAG API。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

from fastapi import APIRouter

from app.api.deps import SessionDep
from app.core.config import settings
from app.generation.llm import LLMError, get_llm_client
from app.generation.rag import RAGService
from app.retrieval.embedding import get_embedding_client
from app.schemas.rag import RAGRequest, RAGResponse

router = APIRouter(prefix="/rag", tags=["rag"])


@router.post(
    "/answer",
    response_model=RAGResponse,
    summary="基于知识库回答问题（实验性慢接口）",
    description=(
        "同步执行 Vector Search + LLM。默认 OMLX 模型可能耗时 11–60 秒，"
        "超过 RAG_REQUEST_TIMEOUT 会返回 503；暂不建议用于交互式生产场景。"
    ),
)
def answer(
    payload: RAGRequest,
    session: SessionDep,
) -> RAGResponse:
    """实验性慢接口：真实推理模型可能耗时数十秒，超时统一返回 503。"""
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            RAGService.answer,
            session,
            question=payload.question,
            embedding_client=get_embedding_client(),
            llm_factory=get_llm_client,
            top_k=payload.top_k,
            source_types=payload.source_types,
            max_distance=payload.max_distance,
        )
        try:
            return future.result(timeout=settings.rag_request_timeout)
        except FutureTimeoutError as exc:
            raise LLMError(
                "RAG 请求超时",
                detail={"timeout_seconds": settings.rag_request_timeout},
            ) from exc


__all__ = ["router"]
