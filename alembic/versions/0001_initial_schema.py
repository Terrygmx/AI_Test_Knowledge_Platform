"""initial schema：业务实体四张表

Revision ID: 0001
Revises: None
Create Date: 2026-10-16

范围说明（与开发计划对齐）：
- 本迁移只包含 **Phase 1 落地**的四张业务实体表；
- ``knowledge_chunks``（Phase 2：检索单元 / 向量）与评测三张表
  （Phase 12：evaluation_cases / evaluation_results / badcases）
  由后续迁移创建，避免「表先于代码存在」造成的漂移。

实现要点：
- 直接复用 ``app.models.types`` 里的可移植类型（``pk_type`` / ``TextArray`` / ``JSONMap``），
  因此同一份迁移既能跑在 PostgreSQL 上，也能跑在 SQLite 上（测试用）；
- ``tags`` / ``related_cases`` / ``metadata`` 只在 ORM 侧设 Python 默认值，
  不设 server_default：写路径一律走 ORM，DDL 不需要为手工 INSERT 兜底；
- 时间戳用 ``func.now()``（按方言编译成 PG 的 ``now()`` / SQLite 的 ``CURRENT_TIMESTAMP``）；
- pgvector 扩展只在 PostgreSQL 上创建（SQLite 没有扩展概念），
  放这里而不是 docker 初始化脚本，是为了让「环境准备」也纳入版本管理。
  注意：``CREATE EXTENSION`` 需要 superuser，托管数据库（如 RDS）可能没有权限，
  那种场景请让 DBA 预先装好扩展，再注释掉这两行。
- ``downgrade`` 不删扩展：扩展可能被其他库 / 其他迁移共用，属于环境而非本迁移的状态。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.models.types import JSONMap, TextArray, pk_type

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if _is_postgres():
        # pgvector：向量检索（Phase 2 的 knowledge_chunks.embedding 依赖它）
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        # pg_trgm：中文三元组模糊匹配兜底
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ---------------------------------------------------------------- 测试用例
    op.create_table(
        "test_cases",
        sa.Column("id", pk_type(), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("module", sa.String(length=64), nullable=True),
        sa.Column("sub_module", sa.String(length=64), nullable=True),
        sa.Column("version", sa.String(length=32), nullable=True),
        sa.Column("priority", sa.String(length=8), nullable=True),
        sa.Column("case_type", sa.String(length=32), nullable=True),
        sa.Column("precondition", sa.Text(), nullable=True),
        sa.Column("steps", sa.Text(), nullable=True),
        sa.Column("expected_result", sa.Text(), nullable=True),
        sa.Column("tags", TextArray(), nullable=False),
        sa.Column("source", sa.String(length=256), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id"),
    )
    op.create_index("idx_test_cases_filter", "test_cases", ["module", "priority", "version"])
    op.create_index("idx_test_cases_tags", "test_cases", ["tags"], postgresql_using="gin")

    # ---------------------------------------------------------------- 缺陷
    op.create_table(
        "defects",
        sa.Column("id", pk_type(), nullable=False),
        sa.Column("bug_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("module", sa.String(length=64), nullable=True),
        sa.Column("version", sa.String(length=32), nullable=True),
        sa.Column("severity", sa.String(length=16), nullable=True),
        sa.Column("priority", sa.String(length=8), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("reproduce_steps", sa.Text(), nullable=True),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("related_cases", TextArray(), nullable=False),
        sa.Column("source", sa.String(length=256), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bug_id"),
    )
    op.create_index("idx_defects_filter", "defects", ["module", "severity", "status", "version"])

    # ---------------------------------------------------------------- 需求
    op.create_table(
        "requirements",
        sa.Column("id", pk_type(), nullable=False),
        sa.Column("req_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("module", sa.String(length=64), nullable=True),
        sa.Column("version", sa.String(length=32), nullable=True),
        sa.Column("priority", sa.String(length=8), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=256), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("req_id"),
    )
    op.create_index("idx_requirements_filter", "requirements", ["module", "version"])

    # ---------------------------------------------------------------- 文档
    op.create_table(
        "documents",
        sa.Column("id", pk_type(), nullable=False),
        sa.Column("doc_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("doc_type", sa.String(length=32), nullable=True),
        sa.Column("path", sa.String(length=512), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("version", sa.String(length=32), nullable=True),
        sa.Column("metadata", JSONMap(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("doc_id"),
    )
    op.create_index("idx_documents_hash", "documents", ["content_hash"])
    op.create_index("idx_documents_version", "documents", ["version"])


def downgrade() -> None:
    op.drop_index("idx_documents_version", table_name="documents")
    op.drop_index("idx_documents_hash", table_name="documents")
    op.drop_table("documents")

    op.drop_index("idx_requirements_filter", table_name="requirements")
    op.drop_table("requirements")

    op.drop_index("idx_defects_filter", table_name="defects")
    op.drop_table("defects")

    op.drop_index("idx_test_cases_tags", table_name="test_cases")
    op.drop_index("idx_test_cases_filter", table_name="test_cases")
    op.drop_table("test_cases")
