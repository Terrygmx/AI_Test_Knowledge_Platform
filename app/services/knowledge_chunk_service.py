"""KnowledgeChunk 服务：创建检索单元、生成 Embedding、查询向量。

关键边界：

- 入参不接收向量，Embedding 由服务层生成，避免调用方塞入不可追溯的向量；
- 内容哈希未传时由服务层计算；
- 向量维度必须与配置一致，否则在写入前拒绝；
- 向量距离排序只在 PostgreSQL 执行，SQLite 返回明确错误而不是伪装成向量检索。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import DomainError, DuplicateError, NotFoundError
from app.core.logging import get_logger
from app.models import KnowledgeChunk
from app.models.types import POSTGRES
from app.retrieval.embedding import EmbeddingClient, content_sha256
from app.schemas.knowledge_chunk import KnowledgeChunkCreate

logger = get_logger(__name__)


class KnowledgeChunkService:
    """检索单元的写入与向量读取。"""

    #: 与 ``KnowledgeChunk.embedding`` 的 ``Vector(1024)`` 保持一致
    embedding_dimension = 1024

    @classmethod
    def create(
        cls,
        session: Session,
        *,
        data: KnowledgeChunkCreate,
        embedding_client: EmbeddingClient | None = None,
    ) -> KnowledgeChunk:
        """创建 Chunk。

        ``embedding_client`` 为 None 时只保存正文与 Metadata，embedding 留空；
        显式传入客户端时立即生成向量。这样 Phase 2 可以在没有 Key 的环境中保存数据，
        也便于测试纯 CRUD 路径。
        """
        values = data.to_orm_dict()
        values["content_hash"] = values.get("content_hash") or content_sha256(data.content)
        values["meta"] = values.pop("metadata", {})

        if embedding_client is not None:
            vector = embedding_client.embed_query(data.content)
            cls._validate_dimension(vector, cls.embedding_dimension)
            values["embedding"] = vector

        chunk = KnowledgeChunk(**values)
        session.add(chunk)
        cls._commit(
            session,
            source_type=data.source_type,
            source_id=data.source_id,
            chunk_index=data.chunk_index,
        )
        logger.info(
            "创建知识片段 source=%s:%s chunk=%s",
            chunk.source_type,
            chunk.source_id,
            chunk.chunk_index,
        )
        return chunk

    @classmethod
    def create_many(
        cls,
        session: Session,
        *,
        items: Sequence[KnowledgeChunkCreate],
        embedding_client: EmbeddingClient,
        replace_source_types: Sequence[str] | None = None,
    ) -> list[KnowledgeChunk]:
        """批量创建，并为所有 Chunk 生成 Embedding。

        ``replace_source_types`` 用于索引重建：先完成 Embedding，再在同一事务中
        删除旧 Chunk。上游失败时不会提前提交删除。
        """
        if not items:
            return []
        vectors = embedding_client.embed_texts([item.content for item in items])
        for vector in vectors:
            cls._validate_dimension(vector, cls.embedding_dimension)

        if replace_source_types:
            session.execute(
                delete(KnowledgeChunk).where(
                    KnowledgeChunk.source_type.in_(list(replace_source_types))
                )
            )

        chunks: list[KnowledgeChunk] = []
        for item, vector in zip(items, vectors, strict=True):
            values = item.to_orm_dict()
            values["content_hash"] = values.get("content_hash") or content_sha256(item.content)
            values["meta"] = values.pop("metadata", {})
            values["embedding"] = vector
            chunks.append(KnowledgeChunk(**values))

        session.add_all(chunks)
        cls._commit(session)
        return chunks

    @classmethod
    def list(
        cls,
        session: Session,
        *,
        offset: int = 0,
        limit: int = 20,
        source_type: str | None = None,
        source_id: str | None = None,
    ) -> tuple[list[KnowledgeChunk], int]:
        stmt = select(KnowledgeChunk)
        if source_type:
            stmt = stmt.where(KnowledgeChunk.source_type == source_type)
        if source_id:
            stmt = stmt.where(KnowledgeChunk.source_id == source_id)
        total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = session.scalars(
            stmt.order_by(KnowledgeChunk.id).limit(limit).offset(offset)
        ).all()
        return list(rows), total

    @classmethod
    def get_or_404(cls, session: Session, chunk_id: int) -> KnowledgeChunk:
        chunk = session.get(KnowledgeChunk, chunk_id)
        if chunk is None:
            raise NotFoundError(f"知识片段不存在：id={chunk_id}")
        return chunk

    @classmethod
    def delete(cls, session: Session, chunk: KnowledgeChunk) -> None:
        session.delete(chunk)
        cls._commit(session)

    @classmethod
    def search(
        cls,
        session: Session,
        *,
        query_embedding: Sequence[float],
        limit: int = 10,
        source_types: Sequence[str] | None = None,
    ) -> list[tuple[KnowledgeChunk, float]]:
        """按 cosine distance 返回 ``(chunk, distance)``。

        距离越小越相似。只有 PostgreSQL + pgvector 支持 ``<=>``；SQLite
        下明确报错，避免测试环境返回假排序。
        """
        if session.bind is None or session.bind.dialect.name != POSTGRES:
            raise DomainError(
                "向量检索仅支持 PostgreSQL + pgvector",
                detail={"dialect": session.bind.dialect.name if session.bind else None},
            )
        if limit < 1:
            return []

        vector = list(query_embedding)
        distance = KnowledgeChunk.embedding.cosine_distance(vector)
        stmt = select(KnowledgeChunk, distance.label("distance"))
        if source_types:
            stmt = stmt.where(KnowledgeChunk.source_type.in_(list(source_types)))
        stmt = stmt.order_by(distance).limit(limit)
        return [(chunk, float(value)) for chunk, value in session.execute(stmt).all()]

    @classmethod
    def _validate_dimension(cls, vector: Sequence[float], expected: int) -> None:
        if len(vector) != expected:
            raise DomainError(
                "Embedding 维度与配置不一致",
                detail={"expected": expected, "actual": len(vector)},
            )

    @classmethod
    def _commit(
        cls,
        session: Session,
        *,
        source_type: str | None = None,
        source_id: str | None = None,
        chunk_index: int | None = None,
    ) -> None:
        """把唯一约束冲突转换成统一错误信封（409 duplicate）。"""
        try:
            session.flush()
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            detail = {
                "source_type": source_type,
                "source_id": source_id,
                "chunk_index": chunk_index,
            }
            logger.warning("知识片段写入冲突：%s", detail)
            raise DuplicateError("知识片段已存在", detail=detail) from exc


__all__ = ["KnowledgeChunkService"]
