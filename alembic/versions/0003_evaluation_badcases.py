"""add evaluation badcases：Badcase 闭环持久化

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-24

``evaluation_badcases`` 持久化 Phase 19 的 Badcase 闭环记录。
``status`` 的顺序约束（open → fixing → fixed → verified）由契约层
``app.evaluation.badcase`` 保证；数据库只做 ``badcase_id`` 唯一性兜底。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.models.types import JSONMap, TextArray, pk_type

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evaluation_badcases",
        sa.Column("id", pk_type(), nullable=False),
        sa.Column("badcase_id", sa.String(length=64), nullable=False),
        sa.Column("query_id", sa.String(length=128), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("symptom", sa.Text(), nullable=False),
        sa.Column("root_cause", sa.Text(), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("regression_query_ids", TextArray(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("evaluation_result", JSONMap(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("badcase_id", name="uq_evaluation_badcases_badcase_id"),
    )
    op.create_index(
        "idx_evaluation_badcases_stage_status",
        "evaluation_badcases",
        ["stage", "status"],
    )
    op.create_index(
        "idx_evaluation_badcases_query",
        "evaluation_badcases",
        ["query_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_evaluation_badcases_query", table_name="evaluation_badcases")
    op.drop_index("idx_evaluation_badcases_stage_status", table_name="evaluation_badcases")
    op.drop_table("evaluation_badcases")
