"""统一检索服务测试（Phase 1 最小版）。"""

from __future__ import annotations

import pytest

from app.core.exceptions import DomainError
from app.services.defect_service import DefectService
from app.services.document_service import DocumentService
from app.services.requirement_service import RequirementService
from app.services.search_service import UnifiedSearchService
from app.services.testcase_service import TestCaseService


@pytest.fixture()
def seeded(db_session):
    TestCaseService.create(
        db_session,
        data={
            "case_id": "LOGIN-001",
            "title": "短信验证码登录冒烟",
            "module": "login",
            "priority": "P0",
            "steps": "输入手机号并获取验证码",
        },
    )
    TestCaseService.create(
        db_session,
        data={
            "case_id": "PAY-001",
            "title": "支付失败重试",
            "module": "payment",
            "priority": "P0",
            "steps": "模拟支付失败后点击重试",
        },
    )
    DefectService.create(
        db_session,
        data={
            "bug_id": "BUG-1021",
            "title": "支付失败后未重置订单状态",
            "module": "payment",
            "priority": "P0",
            "severity": "critical",
            "status": "fixed",
            "resolution": "在支付回调中补充状态机重置",
        },
    )
    RequirementService.create(
        db_session,
        data={
            "req_id": "REQ-001",
            "title": "登录模块支持短信验证码",
            "module": "login",
            "priority": "P1",
            "content": "用户可通过短信验证码登录",
        },
    )
    DocumentService.create(
        db_session,
        data={"doc_id": "DOC-001", "title": "支付模块测试规范", "doc_type": "markdown"},
    )
    return db_session


def _ids(hits) -> list[str]:
    return [hit.source_id for hit in hits]


class TestUnifiedSearch:
    def test_search_all_entities(self, db_session, seeded) -> None:
        hits, total = UnifiedSearchService.search(db_session, limit=20)
        assert total == 5
        assert len(hits) == 5
        assert [h.source_type for h in hits] == [
            "testcase",
            "testcase",
            "defect",
            "requirement",
            "document",
        ]
        assert _ids(hits) == ["LOGIN-001", "PAY-001", "BUG-1021", "REQ-001", "DOC-001"]

    def test_total_is_accurate_across_entities(self, db_session, seeded) -> None:
        _, total = UnifiedSearchService.search(db_session, limit=2)
        assert total == 5

    def test_pagination(self, db_session, seeded) -> None:
        hits, total = UnifiedSearchService.search(db_session, limit=2, offset=0)
        assert _ids(hits) == ["LOGIN-001", "PAY-001"]
        hits, total = UnifiedSearchService.search(db_session, limit=2, offset=2)
        assert _ids(hits) == ["BUG-1021", "REQ-001"]
        hits, total = UnifiedSearchService.search(db_session, limit=2, offset=4)
        assert _ids(hits) == ["DOC-001"]

    def test_offset_beyond_end(self, db_session, seeded) -> None:
        hits, total = UnifiedSearchService.search(db_session, limit=10, offset=99)
        assert hits == []
        assert total == 5

    def test_filter_by_module(self, db_session, seeded) -> None:
        hits, total = UnifiedSearchService.search(
            db_session, filters={"module": "payment"}, limit=10
        )
        assert total == 2
        assert {h.source_type for h in hits} == {"testcase", "defect"}

    def test_unsupported_filter_excludes_entity(self, db_session, seeded) -> None:
        """document 没有 priority 列：必须整体排除，而不是忽略条件后返回全部。"""
        hits, total = UnifiedSearchService.search(db_session, filters={"priority": "P0"}, limit=10)
        assert total == 3  # 2 用例 + 1 缺陷；需求是 P1，文档无优先级
        assert "DOC-001" not in _ids(hits)

    def test_module_filter_excludes_documents(self, db_session, seeded) -> None:
        """module=payment 不应把没有模块信息的文档算进来。"""
        hits, total = UnifiedSearchService.search(
            db_session, filters={"module": "payment"}, limit=10
        )
        assert total == 2
        assert {h.source_type for h in hits} == {"testcase", "defect"}

    def test_source_type_restriction(self, db_session, seeded) -> None:
        hits, total = UnifiedSearchService.search(db_session, source_types=["defect"], limit=10)
        assert total == 1
        assert hits[0].source_id == "BUG-1021"

    def test_multiple_source_types(self, db_session, seeded) -> None:
        hits, total = UnifiedSearchService.search(
            db_session, source_types=["testcase", "document"], limit=10
        )
        assert total == 3
        assert {h.source_type for h in hits} == {"testcase", "document"}

    def test_unknown_source_type_raises(self, db_session, seeded) -> None:
        with pytest.raises(DomainError, match="未知检索来源"):
            UnifiedSearchService.search(db_session, source_types=["nope"], limit=10)  # type: ignore[list-item]

    def test_keyword_search_crosses_entities(self, db_session, seeded) -> None:
        hits, total = UnifiedSearchService.search(db_session, q="支付失败", limit=10)
        assert total == 2
        assert {h.source_type for h in hits} == {"testcase", "defect"}

    def test_matched_on_lists_fields(self, db_session, seeded) -> None:
        hits, total = UnifiedSearchService.search(db_session, q="支付失败", limit=10)
        by_id = {h.source_id: h for h in hits}
        assert by_id["PAY-001"].matched_on == ["title", "steps"]
        assert by_id["BUG-1021"].matched_on == ["title"]

    def test_matched_on_covers_body_fields(self, db_session, seeded) -> None:
        hits, _ = UnifiedSearchService.search(db_session, q="验证码", limit=10)
        by_id = {h.source_id: h for h in hits}
        assert by_id["LOGIN-001"].matched_on == ["title", "steps"]
        assert by_id["REQ-001"].matched_on == ["title", "content"]

    def test_snippet_contains_query(self, db_session, seeded) -> None:
        hits, _ = UnifiedSearchService.search(db_session, q="状态机", limit=10)
        hit = next(h for h in hits if h.source_id == "BUG-1021")
        assert hit.snippet is not None
        assert "状态机" in hit.snippet

    def test_hit_fields_are_mapped(self, db_session, seeded) -> None:
        hits, _ = UnifiedSearchService.search(db_session, source_types=["defect"], limit=1)
        hit = hits[0]
        assert hit.source_type == "defect"
        assert hit.source_id == "BUG-1021"
        assert hit.module == "payment"
        assert hit.severity == "critical"
        assert hit.status == "fixed"
        assert hit.priority is not None and hit.priority.value == "P0"

    def test_document_hit_falls_back_to_doc_id_as_title(self, db_session) -> None:
        DocumentService.create(db_session, data={"doc_id": "DOC-777", "doc_type": "pdf"})
        hits, _ = UnifiedSearchService.search(db_session, source_types=["document"], limit=5)
        hit = next(h for h in hits if h.source_id == "DOC-777")
        assert hit.title == "DOC-777"

    def test_no_results(self, db_session, seeded) -> None:
        hits, total = UnifiedSearchService.search(db_session, q="不存在的关键词xyz", limit=10)
        assert (hits, total) == ([], 0)

    def test_zero_limit_returns_no_rows_but_accurate_total(self, db_session, seeded) -> None:
        hits, total = UnifiedSearchService.search(db_session, limit=0)
        assert hits == []
        assert total == 5

    def test_limit_smaller_than_first_entity_total_is_still_accurate(
        self, db_session, seeded
    ) -> None:
        """取满一页后仍要继续统计后续实体的 total。"""
        hits, total = UnifiedSearchService.search(db_session, limit=1)
        assert _ids(hits) == ["LOGIN-001"]
        assert total == 5

    def test_offset_beyond_first_entity_spills_into_next(self, db_session, seeded) -> None:
        hits, total = UnifiedSearchService.search(db_session, limit=3, offset=1)
        assert _ids(hits) == ["PAY-001", "BUG-1021", "REQ-001"]
        assert total == 5

    def test_pagination_covers_every_row_exactly_once(self, db_session, seeded) -> None:
        """逐页取完，结果集合应等于全量且无重复。"""
        seen: list[str] = []
        for offset in range(0, 6):
            hits, total = UnifiedSearchService.search(db_session, limit=1, offset=offset)
            seen.extend(_ids(hits))
            assert total == 5
        assert seen == ["LOGIN-001", "PAY-001", "BUG-1021", "REQ-001", "DOC-001"]
