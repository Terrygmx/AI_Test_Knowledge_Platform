"""文档 ORM 实体（``documents`` 表）。

只存**元数据**：标题、类型、路径、内容哈希、版本。
正文切分后的检索单元在 ``knowledge_chunks``（Phase 2 引入），
``content_hash`` 用于 Phase 21 增量索引判断「新增 / 修改 / 未变」。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.types import JSONMap, pk_type


class Document(Base):
    """文档元数据。"""

    __tablename__ = "documents"
    __table_args__ = (
        Index("idx_documents_hash", "content_hash"),
        Index("idx_documents_version", "version"),
    )

    id: Mapped[int] = mapped_column(pk_type(), primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    doc_type: Mapped[str | None] = mapped_column(String(32))
    path: Mapped[str | None] = mapped_column(String(512))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[str | None] = mapped_column(String(32))
    # 注意：SQLAlchemy 把 metadata 属性名留给 MetaData，故 Python 侧用 meta，
    # 列名仍是 metadata；Pydantic Schema 通过 validation_alias 对齐回 metadata。
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONMap, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __init__(self, **kwargs: Any) -> None:
        # 防御：Document(metadata={...}) 不会报错，而是把类级 MetaData 覆盖成 dict，
        # 之后 Alembic / create_all 会以诡异方式失败。这里直接fail fast。
        if "metadata" in kwargs:
            raise TypeError(
                "Document 不接受 metadata= 参数：该属性名被 SQLAlchemy 的 MetaData 占用，"
                "请使用 meta= （对应数据库列 metadata）。"
            )
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        return f"<Document {self.doc_id} {self.title!r}>"
