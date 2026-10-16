"""缺陷 ORM 实体（``defects`` 表）。

缺陷与用例通过 ``related_cases``（用例编号数组）弱关联，
不建外键：用例可能被删除或尚未录入，检索时要求「能找到缺陷」比「保证引用完整」更重要。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.types import TextArray, pk_type


class Defect(Base):
    """缺陷。"""

    __tablename__ = "defects"
    __table_args__ = (Index("idx_defects_filter", "module", "severity", "status", "version"),)

    id: Mapped[int] = mapped_column(pk_type(), primary_key=True, autoincrement=True)
    bug_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    module: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[str | None] = mapped_column(String(32))
    severity: Mapped[str | None] = mapped_column(String(16))
    priority: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str | None] = mapped_column(String(32))
    reproduce_steps: Mapped[str | None] = mapped_column(Text)
    root_cause: Mapped[str | None] = mapped_column(Text)
    resolution: Mapped[str | None] = mapped_column(Text)
    related_cases: Mapped[list[str]] = mapped_column(TextArray, nullable=False, default=list)
    source: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<Defect {self.bug_id} {self.title!r}>"
