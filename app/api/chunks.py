"""KnowledgeChunk API：保存检索单元，不暴露原始向量。"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import PaginationDep, SessionDep
from app.models import KnowledgeChunk
from app.schemas.common import Page
from app.schemas.knowledge_chunk import KnowledgeChunkCreate, KnowledgeChunkRead, SourceType
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


@router.get("", response_model=Page[KnowledgeChunkRead], summary="知识片段列表")
def list_chunks(
    pagination: PaginationDep,
    session: SessionDep,
    source_type: SourceType | None = None,
    source_id: str | None = None,
    has_embedding: bool | None = None,
) -> Page[KnowledgeChunkRead]:
    items, total = KnowledgeChunkService.list(
        session,
        offset=pagination.offset,
        limit=pagination.limit,
        source_type=source_type,
        source_id=source_id,
        has_embedding=has_embedding,
    )
    return Page[KnowledgeChunkRead].build(
        items=items, total=total, limit=pagination.limit, offset=pagination.offset
    )


@router.delete("/{chunk_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除知识片段")
def delete_chunk(chunk_id: int, session: SessionDep) -> None:
    KnowledgeChunkService.delete(session, KnowledgeChunkService.get_or_404(session, chunk_id))


__all__ = ["router"]
