#!/usr/bin/env python3
"""把业务实体索引为 KnowledgeChunk。

用法：
    uv run python scripts/index_entities.py --dry-run
    uv run python scripts/index_entities.py
    uv run python scripts/index_entities.py --only testcase,defect

设计取舍：

- 默认使用真实 Embedding（需要 EMBEDDING_API_KEY）；不使用 Fake，避免把测试向量写入知识库；
- 重建时按来源删除旧 Chunk 再写入，保证重复执行不会触发唯一约束；
- ``--dry-run`` 只统计将索引的实体数量，不调用 Embedding、不写数据库；
- 文档正文暂不在此脚本中解析；Phase 5 接入 Parser / Chunker 后再补 document 内容来源。
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


@dataclass
class IndexStats:
    testcase: int = 0
    defect: int = 0
    requirement: int = 0


def _parse_only(value: str | None) -> set[str]:
    allowed = {"testcase", "defect", "requirement"}
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


def rebuild(session: Session, *, only: set[str], dry_run: bool) -> IndexStats:
    items = _collect(session, only)
    stats = _stats(items)
    if dry_run:
        return stats

    if not items:
        session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.source_type.in_(sorted(only))))
        session.commit()
        return stats

    from app.services.knowledge_chunk_service import KnowledgeChunkService

    # 先完成 Embedding 再删除旧索引，避免上游失败时留下空索引。
    KnowledgeChunkService.create_many(
        session,
        items=items,
        embedding_client=get_embedding_client(),
        replace_source_types=only,
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="重建测试知识索引")
    parser.add_argument(
        "--only",
        help="只索引指定来源，逗号分隔：testcase,defect,requirement",
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计，不调用 Embedding / 写库")
    args = parser.parse_args()

    only = _parse_only(args.only)
    with SessionLocal() as session:
        stats = rebuild(session, only=only, dry_run=args.dry_run)

    action = "将索引" if args.dry_run else "已索引"
    print(
        f"{action}：testcase={stats.testcase}, defect={stats.defect}, "
        f"requirement={stats.requirement}"
    )


if __name__ == "__main__":
    main()
