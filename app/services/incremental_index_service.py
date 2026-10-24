"""Phase 21 增量索引执行器：把变更计划真正落到 ``knowledge_chunks``。

边界：

- 计划仍由 ``app.ingestion.incremental.plan_incremental_index`` 生成，本模块
  只负责执行 CREATE / UPDATE / DELETE；两者保持解耦，便于 dry-run 审计；
- 执行范围限定在 desired 中出现的 ``source_type``，避免把 scope 之外的
  document Chunk 误判为 DELETE；
- 实体类型（testcase / defect / requirement）当前是一实体一 Chunk，hash 对比
  按 ``(source_type, source_id)`` 取该实体的 chunk_index=0 内容 hash；
- Embedding 使用调用方传入的客户端，执行器不自行选择模型。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.ingestion.incremental import (
    ChangeKind,
    IndexChangePlan,
    SourceState,
    plan_incremental_index,
)
from app.models import Defect, KnowledgeChunk, Requirement, TestCase
from app.retrieval.embedding import EmbeddingClient
from app.schemas.knowledge_chunk import KnowledgeChunkCreate
from app.services.index_service import case_to_chunk, defect_to_chunk, requirement_to_chunk
from app.services.knowledge_chunk_service import KnowledgeChunkService


@dataclass
class IncrementalSyncResult:
    """一次增量同步的执行统计。"""

    created: int = 0
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0
    embedded: int = 0
    plan: IndexChangePlan | None = None


def desired_states_from_entities(
    session: Session,
    *,
    source_types: set[str] | None = None,
) -> list[SourceState]:
    """从业务实体表构建期望状态，内容组装与全量索引保持同一实现。"""
    allowed = source_types or {"testcase", "defect", "requirement"}
    states: list[SourceState] = []
    if "testcase" in allowed:
        states.extend(
            SourceState(
                source_type="testcase",
                source_id=case.case_id,
                content=case_to_chunk(case).content,
            )
            for case in session.scalars(select(TestCase)).all()
        )
    if "defect" in allowed:
        states.extend(
            SourceState(
                source_type="defect",
                source_id=defect.bug_id,
                content=defect_to_chunk(defect).content,
            )
            for defect in session.scalars(select(Defect)).all()
        )
    if "requirement" in allowed:
        states.extend(
            SourceState(
                source_type="requirement",
                source_id=requirement.req_id,
                content=requirement_to_chunk(requirement).content,
            )
            for requirement in session.scalars(select(Requirement)).all()
        )
    return states


def existing_entity_hashes(
    session: Session, source_types: set[str]
) -> dict[tuple[str, str], str | None]:
    """读取库内实体级 hash；document Chunk 不在实体同步范围内。"""
    rows = session.execute(
        select(KnowledgeChunk).where(KnowledgeChunk.source_type.in_(sorted(source_types)))
    ).scalars()
    hashes: dict[tuple[str, str], str | None] = {}
    for chunk in rows:
        key = (chunk.source_type, chunk.source_id)
        # 一实体一 Chunk：同一实体出现多行时以最小 chunk_index 为准。
        if key not in hashes or chunk.chunk_index < 0:
            hashes[key] = chunk.content_hash
    return hashes


def _delete_source(session: Session, source_type: str, source_id: str) -> int:
    result = session.execute(
        delete(KnowledgeChunk).where(
            KnowledgeChunk.source_type == source_type,
            KnowledgeChunk.source_id == source_id,
        )
    )
    return result.rowcount or 0


def _create_chunk(
    session: Session,
    state: SourceState,
    embedding_client: EmbeddingClient,
) -> None:
    KnowledgeChunkService.create(
        session,
        data=KnowledgeChunkCreate(
            source_type=state.source_type,  # type: ignore[arg-type]
            source_id=state.source_id,
            chunk_index=0,
            content=state.content,
        ),
        embedding_client=embedding_client,
    )


def sync_incremental_index(
    session: Session,
    *,
    desired: list[SourceState],
    embedding_client: EmbeddingClient,
    source_types: set[str] | None = None,
) -> IncrementalSyncResult:
    """生成计划并执行：CREATE / UPDATE 重新生成向量，DELETE 清理残留。

    ``source_types`` 是本次同步的范围声明；不能从 desired 推导——
    当范围内实体被全部删除时，desired 为空集合，DELETE 会被漏掉。
    """
    scope = source_types or {state.source_type for state in desired}
    plan = plan_incremental_index(desired, existing_entity_hashes(session, scope))
    result = IncrementalSyncResult(plan=plan)

    for change in plan.pending_changes:
        if change.kind is ChangeKind.DELETE:
            result.deleted += _delete_source(session, change.source_type, change.source_id)
            session.commit()
            continue

        state = next(
            item
            for item in desired
            if item.source_type == change.source_type and item.source_id == change.source_id
        )
        if change.kind is ChangeKind.UPDATE:
            result.updated += _delete_source(session, change.source_type, change.source_id)
        _create_chunk(session, state, embedding_client)
        if change.kind is ChangeKind.CREATE:
            result.created += 1
        else:
            result.embedded += 1

    result.unchanged = len(plan.changes) - len(plan.pending_changes)
    return result
