"""KnowledgeChunk API：保存检索单元，不暴露原始向量。"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import SessionDep
from app.models import KnowledgeChunk
from app.schemas.knowledge_chunk import KnowledgeChunkCreate, KnowledgeChunkRead
from app.services.knowledge_chunk_service import KnowledgeChunkService

router = APIRouter(prefix="/chunks", tags=["chunks"])


@router.post(
    "",
    response_model=KnowledgeChunkRead,
    status_code=status.HTTP_201_CREATED,
    summary="创建知识片段（不生成 Embedding）",
)
def create_chunk(payload: KnowledgeChunkCreate, session: SessionDep) -> KnowledgeChunk:
    """只保存正文和 Metadata。

    向量由索引任务生成，避免 HTTP 入参不稳定或调用方塞入不可追溯的向量。
    """
    return KnowledgeChunkService.create(session, data=payload)


__all__ = ["router"]

