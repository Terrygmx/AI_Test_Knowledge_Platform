"""测试用例 ORM 实体（``test_cases`` 表）。

**本表是原始事实来源（Source of Truth）**，不是检索单元。
检索用的表示在 ``knowledge_chunks``（Phase 2 引入）：
一条用例可能对应一个 chunk，但二者不能混为一谈——
方案「结构化优先」原则要求业务实体与检索表示分开保存。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.types import TextArray, pk_type


class TestCase(Base):
    """测试用例。"""

    __test__ = False  # 类名以 Test 开头，pytest 会误当作测试类收集，显式排除

    __tablename__ = "test_cases"
    __table_args__ = (
        # 元数据过滤三件套（方案 §10：module / priority / version）
        Index("idx_test_cases_filter", "module", "priority", "version"),
        # tags 为 TEXT[]，PG 上走 GIN 索引
        Index("idx_test_cases_tags", "tags", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(pk_type(), primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    module: Mapped[str | None] = mapped_column(String(64))
    sub_module: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[str | None] = mapped_column(String(32))
    priority: Mapped[str | None] = mapped_column(String(8))
    case_type: Mapped[str | None] = mapped_column(String(32))
    precondition: Mapped[str | None] = mapped_column(Text)
    steps: Mapped[str | None] = mapped_column(Text)
    expected_result: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(TextArray, nullable=False, default=list)
    source: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<TestCase {self.case_id} {self.title!r}>"
