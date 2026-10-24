"""Badcase 闭环 ORM 实体（``evaluation_badcases`` 表）。

Phase 19 的 ``Badcase`` Pydantic 契约是唯一的业务语义来源；本表只负责持久化。
``status`` 的推进顺序与 ``verified`` 必须附带评测结果的约束由契约层保证，
数据库只做存储与唯一性兜底。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.types import JSONMap, TextArray, pk_type


class EvaluationBadcase(Base):
    """一条持久化的 Badcase 闭环记录。"""

    __tablename__ = "evaluation_badcases"
    __table_args__ = (
        Index("idx_evaluation_badcases_stage_status", "stage", "status"),
        Index("idx_evaluation_badcases_query", "query_id"),
    )

    id: Mapped[int] = mapped_column(pk_type(), primary_key=True, autoincrement=True)
    badcase_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    query_id: Mapped[str] = mapped_column(String(128), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    symptom: Mapped[str] = mapped_column(Text, nullable=False)
    root_cause: Mapped[str] = mapped_column(Text, nullable=False)
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    regression_query_ids: Mapped[list[str]] = mapped_column(TextArray, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    evaluation_result: Mapped[dict[str, Any] | None] = mapped_column(JSONMap, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
