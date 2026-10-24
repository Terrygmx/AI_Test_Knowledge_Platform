"""Phase 21 增量索引计划测试。"""

from __future__ import annotations

import pytest

from app.ingestion.incremental import (
    ChangeKind,
    SourceState,
    plan_incremental_index,
)
from app.retrieval.embedding import content_sha256


def _state(source_id: str, content: str) -> SourceState:
    return SourceState(source_type="testcase", source_id=source_id, content=content)


def test_plan_creates_updates_and_unchanged() -> None:
    plan = plan_incremental_index(
        [
            _state("A", "same content"),
            _state("B", "new content"),
            _state("C", "changed content"),
        ],
        {
            ("testcase", "A"): content_sha256("same content"),
            ("testcase", "B"): None,
            ("testcase", "C"): content_sha256("old content"),
        },
    )

    kinds = {(change.source_id, change.kind) for change in plan.changes}
    assert ("A", ChangeKind.UNCHANGED) in kinds
    assert ("B", ChangeKind.CREATE) in kinds
    assert ("C", ChangeKind.UPDATE) in kinds
    assert plan.pending_changes
    assert plan.requires_embedding_count == 2


def test_plan_marks_missing_sources_for_delete() -> None:
    plan = plan_incremental_index(
        [_state("A", "content")],
        {
            ("testcase", "A"): content_sha256("content"),
            ("testcase", "GONE"): content_sha256("old"),
            ("defect", "GONE-2"): None,
        },
    )

    deletes = [change for change in plan.changes if change.kind is ChangeKind.DELETE]
    assert {(change.source_type, change.source_id) for change in deletes} == {
        ("testcase", "GONE"),
        ("defect", "GONE-2"),
    }
    assert all(not change.requires_embedding for change in deletes)


def test_hash_is_stable_and_sensitive_to_content() -> None:
    left = _state("A", "内容一")
    right = _state("A", "内容一")
    changed = _state("A", "内容二")

    assert left.content_hash == right.content_hash
    assert left.content_hash != changed.content_hash


def test_delete_only_when_source_absent_from_desired() -> None:
    plan = plan_incremental_index([], {("testcase", "X"): content_sha256("old")})

    assert len(plan.pending_changes) == 1
    assert plan.pending_changes[0].kind is ChangeKind.DELETE


def test_unsupported_kind_raises() -> None:
    with pytest.raises(ValueError):
        ChangeKind("replace")
