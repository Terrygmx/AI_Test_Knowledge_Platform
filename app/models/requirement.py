"""需求 / 产品文档 ORM 实体（``requirements`` 表）。

正文存在本表（需求通常较短且需要整篇阅读）；
Markdown / PDF 等大文档走 ``documents`` + ``knowledge_chunks``，正文不入库。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.types import pk_type


class Requirement(Base):
    """需求。"""

    __tablename__ = "requirements"
    __table_args__ = (Index("idx_requirements_filter", "module", "version"),)

    id: Mapped[int] = mapped_column(pk_type(), primary_key=True, autoincrement=True)
    req_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    module: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[str | None] = mapped_column(String(32))
    priority: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str | None] = mapped_column(String(32))
    content: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<Requirement {self.req_id} {self.title!r}>"
