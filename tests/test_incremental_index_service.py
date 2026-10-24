"""Phase 21 增量索引执行器测试：计划 → 执行 → 复跑幂等。"""

from __future__ import annotations

from sqlalchemy import select

from app.ingestion.incremental import ChangeKind
from app.models import Defect, KnowledgeChunk, TestCase
from app.retrieval.embedding import FakeEmbeddingClient
from app.services.incremental_index_service import (
    desired_states_from_entities,
    existing_entity_hashes,
    sync_incremental_index,
)


def _seed(session, case_id: str = "LOGIN-001") -> None:
    session.add(
        TestCase(
            case_id=case_id,
            title="登录失败",
            module="login",
            steps="打开登录页",
            expected_result="提示错误",
        )
    )
    session.commit()


def _sync(session):
    return sync_incremental_index(
        session,
        desired=desired_states_from_entities(session),
        embedding_client=FakeEmbeddingClient(dimension=1024),
        source_types={"testcase", "defect", "requirement"},
    )


def test_first_sync_creates_and_embeds(db_session) -> None:
    _seed(db_session)

    result = _sync(db_session)

    assert result.created == 1
    chunk = db_session.scalars(select(KnowledgeChunk)).one()
    assert chunk.source_type == "testcase"
    assert chunk.source_id == "LOGIN-001"
    assert chunk.embedding is not None


def test_rerun_without_changes_is_noop(db_session) -> None:
    _seed(db_session)
    _sync(db_session)

    result = _sync(db_session)

    assert result.created == 0
    assert result.updated == 0
    assert result.deleted == 0
    assert result.unchanged == 1


def test_content_change_updates_and_reembeds(db_session) -> None:
    _seed(db_session)
    _sync(db_session)
    case = db_session.scalars(select(TestCase)).one()
    case.expected_result = "提示错误并保留输入"
    db_session.commit()

    result = _sync(db_session)

    assert result.updated == 1
    chunks = list(db_session.scalars(select(KnowledgeChunk)).all())
    assert len(chunks) == 1
    assert "保留输入" in chunks[0].content
    assert chunks[0].embedding is not None


def test_deleted_entity_is_cleaned(db_session) -> None:
    _seed(db_session)
    _sync(db_session)
    case = db_session.scalars(select(TestCase)).one()
    db_session.delete(case)
    db_session.commit()

    result = _sync(db_session)

    assert result.deleted == 1
    assert list(db_session.scalars(select(KnowledgeChunk)).all()) == []


def test_plan_kinds_reported_in_result(db_session) -> None:
    _seed(db_session)
    _sync(db_session)
    db_session.add(
        Defect(bug_id="BUG-001", title="验证码失效", root_cause="缓存过期")
    )
    db_session.commit()

    result = _sync(db_session)

    kinds = {change.kind for change in (result.plan.changes if result.plan else [])}
    assert ChangeKind.CREATE in kinds
    assert ChangeKind.UNCHANGED in kinds


def test_desired_states_cover_all_entity_types(db_session) -> None:
    _seed(db_session)
    db_session.add(Defect(bug_id="BUG-001", title="验证码失效"))
    db_session.commit()

    states = desired_states_from_entities(db_session)

    assert {(s.source_type, s.source_id) for s in states} == {
        ("testcase", "LOGIN-001"),
        ("defect", "BUG-001"),
    }
    assert existing_entity_hashes(db_session, {"testcase", "defect"}) == {}
