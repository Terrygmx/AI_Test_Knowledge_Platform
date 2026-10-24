#!/usr/bin/env python3
"""Phase 21 增量索引执行入口。

用法：
    uv run python scripts/incremental_index.py --dry-run   # 只输出计划
    uv run python scripts/incremental_index.py             # 执行同步（真实 Embedding）
    uv run python scripts/incremental_index.py --only testcase,defect

与 ``index_entities.py`` 的全量重建不同，本脚本按 content hash 只处理
实际发生变化的实体：未变化的实体不重新 Embedding，库内已删除的实体
其 Chunk 会被清理。范围限定在实体类型（testcase / defect / requirement）；
document Chunk 由导入与补向量流程负责。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import SessionLocal
from app.ingestion.incremental import ChangeKind
from app.retrieval.embedding import get_embedding_client
from app.services.incremental_index_service import (
    desired_states_from_entities,
    sync_incremental_index,
)

_ALLOWED = {"testcase", "defect", "requirement"}


def main() -> None:
    parser = argparse.ArgumentParser(description="按 content hash 增量同步实体索引")
    parser.add_argument(
        "--only",
        default=None,
        help="逗号分隔的实体类型，默认全部：testcase,defect,requirement",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只输出变更计划，不执行写入、不调用 Embedding",
    )
    args = parser.parse_args()

    if args.only:
        selected = {part.strip() for part in args.only.split(",") if part.strip()}
        unknown = selected - _ALLOWED
        if unknown:
            parser.error(f"未知实体类型：{sorted(unknown)}，允许 {sorted(_ALLOWED)}")
    else:
        selected = set(_ALLOWED)

    with SessionLocal() as session:
        desired = desired_states_from_entities(session, source_types=selected)
        if args.dry_run:
            from app.ingestion.incremental import plan_incremental_index
            from app.services.incremental_index_service import existing_entity_hashes

            plan = plan_incremental_index(desired, existing_entity_hashes(session, selected))
            print(
                json.dumps(
                    [
                        {
                            "kind": change.kind.value,
                            "source_type": change.source_type,
                            "source_id": change.source_id,
                            "old_hash": change.old_hash,
                            "new_hash": change.new_hash,
                        }
                        for change in plan.changes
                    ],
                    ensure_ascii=False,
                    indent=2,
                )
            )
            pending = plan.pending_changes
            print(
                f"计划：create={sum(c.kind is ChangeKind.CREATE for c in pending)} "
                f"update={sum(c.kind is ChangeKind.UPDATE for c in pending)} "
                f"delete={sum(c.kind is ChangeKind.DELETE for c in pending)} "
                f"unchanged={len(plan.changes) - len(pending)}",
            )
            return

        result = sync_incremental_index(
            session,
            desired=desired,
            embedding_client=get_embedding_client(),
            source_types=selected,
        )
    print(
        f"增量同步完成：created={result.created} updated={result.updated} "
        f"deleted={result.deleted} unchanged={result.unchanged} "
        f"embedded={result.created + result.updated}"
    )


if __name__ == "__main__":
    main()
