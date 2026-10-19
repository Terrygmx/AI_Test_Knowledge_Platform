#!/usr/bin/env python3
"""把业务实体索引为 KnowledgeChunk。

用法：
    uv run python scripts/index_entities.py --dry-run
    uv run python scripts/index_entities.py
    uv run python scripts/index_entities.py --only testcase,defect
    uv run python scripts/index_entities.py --only document            # 只补向量
    uv run python scripts/index_entities.py --only document --rebuild  # 强制重算

设计取舍：

- 默认使用真实 Embedding（需要 EMBEDDING_API_KEY）；不使用 Fake，避免把测试向量写入知识库；
- 重建时按来源删除旧 Chunk 再写入，保证重复执行不会触发唯一约束；
- ``--dry-run`` 只统计将索引的实体数量，不调用 Embedding、不写数据库；
- ``document`` 默认不重建 Chunk：正文由 Phase 5 的 ``ImportService`` / 表格导入切分，
  本脚本只给 ``embedding IS NULL`` 的现有 document Chunk **补向量**（增量）；
- ``--rebuild`` 让 document 也走「重算全部向量」：切分策略或 Embedding 模型变更后需要
  （Phase 6 Chunking 实验会调整切分，旧向量不会被增量补向量刷新）；
- 向量按 ``EMBEDDING_BATCH_SIZE`` 分批提交，上游中途失败时已完成批次保留。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# 直接执行 ``python scripts/index_entities.py`` 时，仓库根不在 sys.path。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.models import Defect, KnowledgeChunk, Requirement, TestCase
from app.retrieval.embedding import get_embedding_client
from app.schemas.knowledge_chunk import KnowledgeChunkCreate
from app.services.index_service import (
    case_to_chunk,
    defect_to_chunk,
    requirement_to_chunk,
)
from app.services.knowledge_chunk_service import KnowledgeChunkService

#: 重建语义（按实体重建 Chunk）的类型；document 走补向量语义
_REBUILD_TYPES = {"testcase", "defect", "requirement"}


@dataclass
class IndexStats:
    testcase: int = 0
    defect: int = 0
    requirement: int = 0
    #: 本次补齐向量的 document Chunk 数
    document: int = 0


def _parse_only(value: str | None) -> set[str]:
    allowed = _REBUILD_TYPES | {"document"}
    if not value:
        return allowed
    selected = {part.strip() for part in value.split(",") if part.strip()}
    unknown = selected - allowed
    if unknown:
        raise ValueError(f"未知实体类型：{sorted(unknown)}，允许 {sorted(allowed)}")
    return selected


def _collect(session: Session, only: set[str]) -> list[KnowledgeChunkCreate]:
    items: list[KnowledgeChunkCreate] = []
    if "testcase" in only:
        items.extend(case_to_chunk(case) for case in session.scalars(select(TestCase)).all())
    if "defect" in only:
        items.extend(defect_to_chunk(defect) for defect in session.scalars(select(Defect)).all())
    if "requirement" in only:
        items.extend(
            requirement_to_chunk(requirement)
            for requirement in session.scalars(select(Requirement)).all()
        )
    return items


def _stats(items: Sequence[KnowledgeChunkCreate]) -> IndexStats:
    stats = IndexStats()
    for item in items:
        setattr(stats, item.source_type, getattr(stats, item.source_type) + 1)
    return stats


def _count_documents(session: Session, *, rebuild: bool) -> int:
    """document 模式下的预期数量：补向量只数空缺，重算则数全部。"""
    if rebuild:
        chunks = list(
            session.scalars(
                select(KnowledgeChunk).where(KnowledgeChunk.source_type == "document")
            ).all()
        )
        # 与 reembed_chunks 一致：空白正文无法生成向量，不计入
        return len([chunk for chunk in chunks if chunk.content.strip()])
    return KnowledgeChunkService.count_without_embedding(session, source_type="document")


def rebuild(
    session: Session,
    *,
    only: set[str],
    dry_run: bool,
    rebuild_all: bool = False,
) -> IndexStats:
    rebuild_types = only & _REBUILD_TYPES
    items = _collect(session, rebuild_types)
    stats = _stats(items)
    stats.document = _count_documents(session, rebuild=rebuild_all) if "document" in only else 0
    if dry_run:
        return stats

    if "document" in only:
        if rebuild_all:
            stats.document = KnowledgeChunkService.reembed_chunks(
                session,
                embedding_client=get_embedding_client(),
                source_types=["document"],
            )
        else:
            stats.document = KnowledgeChunkService.backfill_embeddings(
                session,
                embedding_client=get_embedding_client(),
                source_types=["document"],
            )

    if not items:
        if rebuild_types:
            session.execute(
                delete(KnowledgeChunk).where(
                    KnowledgeChunk.source_type.in_(sorted(rebuild_types))
                )
            )
            session.commit()
        return stats

    # 先完成 Embedding 再删除旧索引，避免上游失败时留下空索引。
    KnowledgeChunkService.create_many(
        session,
        items=items,
        embedding_client=get_embedding_client(),
        replace_source_types=rebuild_types,
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="重建测试知识索引")
    parser.add_argument(
        "--only",
        help=(
            "只处理指定来源，逗号分隔：testcase,defect,requirement,document；"
            "document 为补向量，其余为按实体重建"
        ),
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="document 也重算全部向量（默认只补 embedding 为空的 Chunk）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计，不调用 Embedding / 写库")
    args = parser.parse_args()

    only = _parse_only(args.only)
    with SessionLocal() as session:
        stats = rebuild(
            session, only=only, dry_run=args.dry_run, rebuild_all=args.rebuild
        )

    action = "将索引" if args.dry_run else "已索引"
    print(
        f"{action}：testcase={stats.testcase}, defect={stats.defect}, "
        f"requirement={stats.requirement}, document={stats.document}"
    )


if __name__ == "__main__":
    main()
