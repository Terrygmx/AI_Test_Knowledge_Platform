"""Phase 3 最小 RAG API。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import SessionDep
from app.generation.llm import get_llm_client
from app.generation.rag import RAGService
from app.retrieval.embedding import get_embedding_client
from app.schemas.rag import RAGRequest, RAGResponse

router = APIRouter(prefix="/rag", tags=["rag"])


@router.post("/answer", response_model=RAGResponse, summary="基于知识库回答问题")
def answer(
    payload: RAGRequest,
    session: SessionDep,
) -> RAGResponse:
    return RAGService.answer(
        session,
        question=payload.question,
        embedding_client=get_embedding_client(),
        llm_factory=get_llm_client,
        top_k=payload.top_k,
        source_types=payload.source_types,
    )


__all__ = ["router"]
