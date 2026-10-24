"""检索单元 ORM 实体（``knowledge_chunks`` 表）。

**原始业务实体是事实来源，本表只是检索表示。**
TestCase / Defect / Requirement / Document 的正文可以被重新切分、重新 Embedding，
但 Citation 最终仍必须锚定到来源业务编号（Case ID / Bug ID / Req ID / Doc ID）。

设计要点：

- ``source_type`` + ``source_id`` 是逻辑引用，不建外键：文档 / 用例可能先删后补，
  检索要求“能找到证据”，而不是阻止来源删除；
- ``embedding`` 使用 pgvector 的 ``VECTOR(N)``，维度由 ``EMBEDDING_DIM`` 决定；
- SQLite 测试库同样能存取向量（pgvector 类型退化为文本存储），但不支持距离运算；
- ``content_hash`` 用于 Phase 21 判断内容是否变化，避免无意义地重新 Embedding。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.types import JSONMap, pk_type


class KnowledgeChunk(Base):
    """一条可检索的知识片段。"""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint(
            "source_type",
            "source_id",
            "chunk_index",
            name="uq_knowledge_chunks_source_chunk",
        ),
        Index("idx_knowledge_chunks_source", "source_type", "source_id"),
        Index("idx_knowledge_chunks_document", "document_id"),
        Index("idx_knowledge_chunks_content_hash", "content_hash"),
        Index("idx_knowledge_chunks_version_key", "version_key"),
        Index(
            "idx_knowledge_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(pk_type(), primary_key=True, autoincrement=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    document_id: Mapped[str | None] = mapped_column(String(64))
    chunk_index: Mapped[int] = mapped_column(nullable=False, default=0)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONMap, nullable=False, default=dict)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str | None] = mapped_column(String(32))
    #: ``version`` 的零填充可排序键（如 ``0002.0010.0000``）；用于 SQL 版本区间过滤。
    #: 非法版本为 NULL，不匹配任何区间。
    version_key: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __init__(self, **kwargs: Any) -> None:
        # 与 Document 相同的保护：metadata 是 SQLAlchemy MetaData 的保留属性名。
        if "metadata" in kwargs:
            raise TypeError(
                "KnowledgeChunk 不接受 metadata= 参数：该属性名被 SQLAlchemy 的 MetaData 占用，"
                "请使用 meta= （对应数据库列 metadata）。"
            )
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        return (
            f"<KnowledgeChunk {self.source_type}:{self.source_id}"
            f"#{self.chunk_index} {self.content[:30]!r}>"
        )


__all__ = ["KnowledgeChunk"]
