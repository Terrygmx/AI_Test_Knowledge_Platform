"""add knowledge_chunks.version_key：版本区间 SQL 过滤的排序键

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-24

``version_key`` 是 ``version`` 的零填充形式（``v2.10`` -> ``0002.010.000``
风格），让 SQL 普通字符串比较等价于数值比较。非法 / 缺失版本回填 NULL，
与 ``VersionRange.contains`` 拒绝 None / 非法值的语义一致。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.retrieval.versioning import version_key as compute_version_key

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_chunks", sa.Column("version_key", sa.String(16), nullable=True))
    op.create_index(
        "idx_knowledge_chunks_version_key",
        "knowledge_chunks",
        ["version_key"],
    )

    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, version FROM knowledge_chunks WHERE version IS NOT NULL")
    ).fetchall()
    for row_id, version in rows:
        key = compute_version_key(version)
        if key is not None:
            conn.execute(
                sa.text(
                    "UPDATE knowledge_chunks SET version_key = :key WHERE id = :id"
                ),
                {"key": key, "id": row_id},
            )


def downgrade() -> None:
    op.drop_index("idx_knowledge_chunks_version_key", table_name="knowledge_chunks")
    op.drop_column("knowledge_chunks", "version_key")
