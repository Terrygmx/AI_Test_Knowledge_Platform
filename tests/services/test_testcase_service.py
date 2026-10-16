"""实体服务测试（以 TestCase 为主，覆盖通用 CRUD 基类行为）。"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.exceptions import DuplicateError, NotFoundError
from app.models import TestCase
from app.services.testcase_service import TestCaseService

CASE_DATA = {
    "case_id": "LOGIN-001",
    "title": "短信验证码登录冒烟",
    "module": "login",
    "priority": "P0",
    "case_type": "smoke",
    "steps": "1. 输入手机号 2. 获取验证码 3. 点击登录",
    "expected_result": "登录成功并跳转首页",
    "tags": ["冒烟", "登录"],
}


@pytest.fixture()
def seeded(db_session):
    """造 3 条不同模块 / 优先级的用例。"""
    rows = [
        TestCaseService.create(
            db_session,
            data={
                **CASE_DATA,
                "case_id": "LOGIN-001",
                "module": "login",
                "priority": "P0",
                "version": "v1.2",
            },
        ),
        TestCaseService.create(
            db_session,
            data={
                **CASE_DATA,
                "case_id": "LOGIN-002",
                "title": "账号密码登录",
                "module": "login",
                "priority": "P1",
                "version": "v1.3",
            },
        ),
        TestCaseService.create(
            db_session,
            data={
                **CASE_DATA,
                "case_id": "PAY-001",
                "title": "支付失败重试",
                "module": "payment",
                "priority": "P0",
                "version": "v1.3",
            },
        ),
    ]
    return rows


class TestCreate:
    def test_create_persists_and_assigns_id(self, db_session) -> None:
        case = TestCaseService.create(db_session, data=CASE_DATA)
        assert case.id is not None
        assert case.tags == ["冒烟", "登录"]

    def test_create_is_committed(self, db_session, session_factory) -> None:
        TestCaseService.create(db_session, data=CASE_DATA)
        with session_factory() as fresh:
            assert fresh.scalar(select(TestCase).where(TestCase.case_id == "LOGIN-001")) is not None

    def test_duplicate_case_id_raises_with_detail(self, db_session) -> None:
        TestCaseService.create(db_session, data=CASE_DATA)
        with pytest.raises(DuplicateError) as exc_info:
            TestCaseService.create(db_session, data={**CASE_DATA, "title": "另一个标题"})
        assert exc_info.value.detail == {"field": "case_id", "value": "LOGIN-001"}
        assert "LOGIN-001" in exc_info.value.message

    def test_session_usable_after_duplicate(self, db_session) -> None:
        """冲突后必须回滚，会话不能停留在 broken 状态。"""
        TestCaseService.create(db_session, data=CASE_DATA)
        with pytest.raises(DuplicateError):
            TestCaseService.create(db_session, data=CASE_DATA)
        case = TestCaseService.create(db_session, data={**CASE_DATA, "case_id": "LOGIN-009"})
        assert case.case_id == "LOGIN-009"

    def test_integrity_error_fallback_converts_to_duplicate(self, db_session, monkeypatch) -> None:
        """预检被绕过（模拟并发）时，flush 的 IntegrityError 也要变成 409 且会话可用。"""
        TestCaseService.create(db_session, data=CASE_DATA)
        monkeypatch.setattr(
            TestCaseService, "_ensure_unique", classmethod(lambda cls, *a, **k: None)
        )
        with pytest.raises(DuplicateError) as exc_info:
            TestCaseService.create(db_session, data=CASE_DATA)
        assert exc_info.value.detail["value"] == "LOGIN-001"

        # 回滚之后会话仍可正常使用
        restored = TestCaseService.get_by_unique(db_session, "LOGIN-001")
        assert restored is not None


class TestRead:
    def test_get_by_primary_key(self, db_session, seeded) -> None:
        case = TestCaseService.get(db_session, seeded[0].id)
        assert case is not None
        assert case.case_id == "LOGIN-001"

    def test_get_by_unknown_primary_key(self, db_session, seeded) -> None:
        assert TestCaseService.get(db_session, 99999) is None

    def test_get_by_unique(self, db_session, seeded) -> None:
        case = TestCaseService.get_by_unique(db_session, "PAY-001")
        assert case is not None
        assert case.module == "payment"

    def test_get_by_unique_missing_returns_none(self, db_session) -> None:
        assert TestCaseService.get_by_unique(db_session, "NOPE-1") is None

    def test_get_by_unique_or_404(self, db_session, seeded) -> None:
        assert TestCaseService.get_by_unique_or_404(db_session, "PAY-001").case_id == "PAY-001"

    def test_get_by_unique_or_404_raises(self, db_session) -> None:
        with pytest.raises(NotFoundError) as exc_info:
            TestCaseService.get_by_unique_or_404(db_session, "NOPE-1")
        assert exc_info.value.http_status == 404
        assert "NOPE-1" in exc_info.value.message


class TestList:
    def test_list_all_with_pagination(self, db_session, seeded) -> None:
        items, total = TestCaseService.list(db_session, offset=0, limit=2)
        assert total == 3
        assert len(items) == 2
        assert [i.case_id for i in items] == ["LOGIN-001", "LOGIN-002"]

    def test_second_page(self, db_session, seeded) -> None:
        items, total = TestCaseService.list(db_session, offset=2, limit=2)
        assert total == 3
        assert [i.case_id for i in items] == ["PAY-001"]

    def test_filter_by_module(self, db_session, seeded) -> None:
        items, total = TestCaseService.list(
            db_session, offset=0, limit=10, filters={"module": "login"}
        )
        assert total == 2
        assert {i.case_id for i in items} == {"LOGIN-001", "LOGIN-002"}

    def test_filter_by_priority_and_version(self, db_session, seeded) -> None:
        items, total = TestCaseService.list(
            db_session, offset=0, limit=10, filters={"priority": "P0", "version": "v1.3"}
        )
        assert total == 1
        assert items[0].case_id == "PAY-001"

    def test_combined_filters(self, db_session, seeded) -> None:
        items, total = TestCaseService.list(
            db_session, offset=0, limit=10, filters={"module": "login", "priority": "P0"}
        )
        assert total == 1
        assert items[0].case_id == "LOGIN-001"

    def test_unknown_filter_key_is_ignored(self, db_session, seeded) -> None:
        items, total = TestCaseService.list(
            db_session, offset=0, limit=10, filters={"not_a_column": "x"}
        )
        assert total == 3

    def test_none_filter_value_is_ignored(self, db_session, seeded) -> None:
        _, total = TestCaseService.list(db_session, offset=0, limit=10, filters={"module": None})
        assert total == 3

    def test_keyword_search_matches_title(self, db_session, seeded) -> None:
        items, total = TestCaseService.list(db_session, offset=0, limit=10, q="支付失败")
        assert total == 1
        assert items[0].case_id == "PAY-001"

    def test_keyword_search_matches_body_fields(self, db_session, seeded) -> None:
        """三条用例的 steps 都来自同一份模板，均包含"验证码"。"""
        items, total = TestCaseService.list(db_session, offset=0, limit=10, q="验证码")
        assert total == 3
        assert {i.case_id for i in items} == {"LOGIN-001", "LOGIN-002", "PAY-001"}

    def test_keyword_search_is_case_insensitive_for_ascii(self, db_session, seeded) -> None:
        _, total = TestCaseService.list(db_session, offset=0, limit=10, q="PAY-001")
        assert total == 0  # 编号不在 search_fields 内
        _, total = TestCaseService.list(db_session, offset=0, limit=10, q="登录")
        assert total == 3

    def test_empty_result(self, db_session, seeded) -> None:
        items, total = TestCaseService.list(db_session, offset=0, limit=10, q="不存在的关键词")
        assert (items, total) == ([], 0)


class TestUpdate:
    def test_partial_update(self, db_session, seeded) -> None:
        case = TestCaseService.update(db_session, seeded[0], data={"priority": "P2"})
        assert case.priority == "P2"
        assert case.title == CASE_DATA["title"]  # 未传字段不受影响

    def test_update_can_change_case_id(self, db_session, seeded) -> None:
        TestCaseService.update(db_session, seeded[0], data={"case_id": "LOGIN-101"})
        assert TestCaseService.get_by_unique(db_session, "LOGIN-101") is not None
        assert TestCaseService.get_by_unique(db_session, "LOGIN-001") is None

    def test_update_to_existing_case_id_raises(self, db_session, seeded) -> None:
        with pytest.raises(DuplicateError):
            TestCaseService.update(db_session, seeded[0], data={"case_id": "PAY-001"})

    def test_update_same_case_id_is_allowed(self, db_session, seeded) -> None:
        case = TestCaseService.update(db_session, seeded[0], data={"case_id": "LOGIN-001"})
        assert case.case_id == "LOGIN-001"

    def test_update_is_committed(self, db_session, seeded, session_factory) -> None:
        TestCaseService.update(db_session, seeded[0], data={"title": "新标题"})
        with session_factory() as fresh:
            row = TestCaseService.get_by_unique(fresh, "LOGIN-001")
            assert row is not None and row.title == "新标题"


class TestDelete:
    def test_delete(self, db_session, seeded) -> None:
        TestCaseService.delete(db_session, seeded[0])
        assert TestCaseService.get_by_unique(db_session, "LOGIN-001") is None

    def test_delete_is_committed(self, db_session, seeded, session_factory) -> None:
        TestCaseService.delete(db_session, seeded[0])
        with session_factory() as fresh:
            assert TestCaseService.get_by_unique(fresh, "LOGIN-001") is None


class TestDeclaration:
    def test_service_declares_contract(self) -> None:
        assert TestCaseService.model is TestCase
        assert TestCaseService.unique_field == "case_id"
        assert TestCaseService.label == "测试用例"
        assert "title" in TestCaseService.search_fields
        assert "steps" in TestCaseService.search_fields
        assert set(TestCaseService.equality_filters) == {
            "module",
            "sub_module",
            "version",
            "priority",
            "case_type",
        }
