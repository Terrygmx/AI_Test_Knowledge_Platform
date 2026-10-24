"""Phase 21 增量索引：按 content hash 生成变更计划。

本模块只计算“应该做什么”，不执行写入。调用方拿到
``IndexChangePlan`` 后自行调用既有导入 / Embedding 服务，保证
变更决策与执行解耦、可测试。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from app.retrieval.embedding import content_sha256
from app.schemas.common import BaseSchema


class ChangeKind(StrEnum):
    """基于 hash 对比的变更类型。"""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    UNCHANGED = "unchanged"


class SourceState(BaseSchema):
    """一个来源实体的期望状态。"""

    source_type: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    content: str

    @property
    def content_hash(self) -> str:
        return content_sha256(self.content)


class IndexChange(BaseSchema):
    """一条需要执行的变更。"""

    kind: ChangeKind
    source_type: str
    source_id: str
    old_hash: str | None = None
    new_hash: str | None = None

    @property
    def requires_embedding(self) -> bool:
        return self.kind in {ChangeKind.CREATE, ChangeKind.UPDATE}


class IndexChangePlan(BaseSchema):
    """一次增量同步的完整计划。"""

    changes: list[IndexChange] = Field(default_factory=list)

    @property
    def pending_changes(self) -> list[IndexChange]:
        return [change for change in self.changes if change.kind is not ChangeKind.UNCHANGED]

    @property
    def requires_embedding_count(self) -> int:
        return sum(change.requires_embedding for change in self.changes)


def plan_incremental_index(
    desired: list[SourceState],
    existing_hashes: dict[tuple[str, str], str | None],
) -> IndexChangePlan:
    """对比期望状态与库内 hash，生成变更计划。

    ``existing_hashes`` 的 key 是 ``(source_type, source_id)``，值为当前
    content hash；缺失 key 表示库内不存在该来源，需要 CREATE。期望状态中
    不存在的库内来源生成 DELETE，避免旧数据残留。
    """
    desired_keys = {(state.source_type, state.source_id) for state in desired}
    changes: list[IndexChange] = []
    for state in desired:
        key = (state.source_type, state.source_id)
        old_hash = existing_hashes.get(key)
        new_hash = state.content_hash
        if old_hash is None:
            kind = ChangeKind.CREATE
        elif old_hash != new_hash:
            kind = ChangeKind.UPDATE
        else:
            kind = ChangeKind.UNCHANGED
        changes.append(
            IndexChange(
                kind=kind,
                source_type=state.source_type,
                source_id=state.source_id,
                old_hash=old_hash,
                new_hash=new_hash,
            )
        )
    for key, old_hash in existing_hashes.items():
        if key not in desired_keys:
            changes.append(
                IndexChange(
                    kind=ChangeKind.DELETE,
                    source_type=key[0],
                    source_id=key[1],
                    old_hash=old_hash,
                )
            )
    return IndexChangePlan(changes=changes)


__all__ = [
    "ChangeKind",
    "IndexChange",
    "IndexChangePlan",
    "SourceState",
    "plan_incremental_index",
]
