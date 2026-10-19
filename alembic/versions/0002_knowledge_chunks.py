"""add knowledge chunks：检索单元与 pgvector 向量列

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-19

``knowledge_chunks`` 是原始业务实体的检索表示，不是事实来源。
向量列固定为 ``VECTOR(1024)``，与 bge-m3 / ``EMBEDDING_DIM`` 对齐。
HNSW cosine 索引只在 PostgreSQL 创建；SQLite 路径保留普通表以支持离线测试。
"""

from __future__ import annotations

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op
from app.models.types import JSONMap, pk_type

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", pk_type(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.String(length=64), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", JSONMap(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_type",
            "source_id",
            "chunk_index",
            name="uq_knowledge_chunks_source_chunk",
        ),
    )
    op.create_index(
        "idx_knowledge_chunks_source",
        "knowledge_chunks",
        ["source_type", "source_id"],
    )
    op.create_index(
        "idx_knowledge_chunks_document",
        "knowledge_chunks",
        ["document_id"],
    )
    op.create_index(
        "idx_knowledge_chunks_content_hash",
        "knowledge_chunks",
        ["content_hash"],
    )

    if _is_postgresql():
        # hnsw: idx_knowledge_chunks_embedding_hnsw
        op.execute(
            "CREATE INDEX idx_knowledge_chunks_embedding_hnsw "
            "ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)"
        )


def downgrade() -> None:
    if _is_postgresql():
        op.execute("DROP INDEX IF EXISTS idx_knowledge_chunks_embedding_hnsw")
    op.drop_index("idx_knowledge_chunks_content_hash", table_name="knowledge_chunks")
    op.drop_index("idx_knowledge_chunks_document", table_name="knowledge_chunks")
    op.drop_index("idx_knowledge_chunks_source", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
