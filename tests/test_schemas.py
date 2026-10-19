"""Pydantic Schema 测试：字段校验、受控词表规范化、ORM 输出。

重点覆盖两个容易踩坑的点：
1. 枚举入库必须是裸值（``"P0"``），不能是 ``str(Priority.P0)`` 得到的 ``"Priority.P0"``；
2. 业务编号、必填标题的边界（空串、非法字符）必须被 422 拒绝。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.enums import CaseType, DefectStatus, Priority, Severity
from app.schemas.common import Page
from app.schemas.defect import DefectCreate, DefectRead, DefectUpdate
from app.schemas.document import DocumentCreate, DocumentRead
from app.schemas.requirement import RequirementCreate
from app.schemas.test_case import TestCaseCreate, TestCaseRead, TestCaseUpdate


class TestTestCaseCreate:
    def test_minimal_payload(self) -> None:
        payload = TestCaseCreate(case_id="LOGIN-001", title="短信登录冒烟")
        assert payload.case_id == "LOGIN-001"
        assert payload.tags == []
        assert payload.priority is None

    def test_case_id_rejects_whitespace_and_special_chars(self) -> None:
        for bad in ("", "   ", "LOGIN 001", "登录-001", "LOGIN/001"):
            with pytest.raises(ValidationError):
                TestCaseCreate(case_id=bad, title="x")

    def test_title_is_required_and_not_blank(self) -> None:
        with pytest.raises(ValidationError):
            TestCaseCreate(case_id="A-1")  # type: ignore[call-arg]
        with pytest.raises(ValidationError):
            TestCaseCreate(case_id="A-1", title="   ")

    def test_priority_is_normalized_to_canonical_value(self) -> None:
        payload = TestCaseCreate(case_id="A-1", title="x", priority="p0")
        assert payload.priority is Priority.P0

    def test_priority_rejects_unknown_value(self) -> None:
        with pytest.raises(ValidationError):
            TestCaseCreate(case_id="A-1", title="x", priority="P9")

    def test_case_type_is_normalized(self) -> None:
        payload = TestCaseCreate(case_id="A-1", title="x", case_type="Smoke")
        assert payload.case_type is CaseType.SMOKE

    def test_case_type_rejects_unknown_value(self) -> None:
        with pytest.raises(ValidationError):
            TestCaseCreate(case_id="A-1", title="x", case_type="weird")

    def test_tags_are_stripped_deduped_and_empties_dropped(self) -> None:
        payload = TestCaseCreate(
            case_id="A-1", title="x", tags=[" 冒烟 ", "冒烟", "", "   ", "登录"]
        )
        assert payload.tags == ["冒烟", "登录"]

    def test_version_is_stripped(self) -> None:
        payload = TestCaseCreate(case_id="A-1", title="x", version="  v1.3 ")
        assert payload.version == "v1.3"

    def test_to_orm_dict_returns_plain_values(self) -> None:
        """防止 str(Enum) 变成 'Priority.P0' 写入数据库。"""
        payload = TestCaseCreate(case_id="A-1", title="x", priority="p1", case_type="smoke")
        data = payload.to_orm_dict()
        assert data["priority"] == "P1"
        assert data["case_type"] == "smoke"
        assert all(not hasattr(v, "__members__") for v in data.values())


class TestTestCaseUpdate:
    def test_everything_optional(self) -> None:
        payload = TestCaseUpdate()
        assert payload.to_orm_dict(exclude_unset=True) == {}

    def test_partial_update_only_touches_given_fields(self) -> None:
        payload = TestCaseUpdate(priority="p2")
        assert payload.to_orm_dict(exclude_unset=True) == {"priority": "P2"}

    def test_can_clear_optional_field_with_none(self) -> None:
        payload = TestCaseUpdate(module=None)
        assert payload.to_orm_dict(exclude_unset=True) == {"module": None}


class TestTestCaseRead:
    def test_read_from_orm_object(self, db_session) -> None:
        from app.models import TestCase

        case = TestCase(case_id="READ-001", title="读取测试", priority="P0", tags=["a"])
        db_session.add(case)
        db_session.commit()
        db_session.refresh(case)

        read = TestCaseRead.model_validate(case)
        assert read.case_id == "READ-001"
        assert read.priority is Priority.P0
        assert read.tags == ["a"]
        assert read.id is not None
        assert read.created_at is not None


class TestDefectSchemas:
    def test_create_normalizes_enums(self) -> None:
        payload = DefectCreate(
            bug_id="BUG-1021",
            title="支付失败",
            severity="critical",
            status="In_Progress",
            priority="P0",
        )
        assert payload.severity is Severity.CRITICAL
        assert payload.status is DefectStatus.IN_PROGRESS
        assert payload.to_orm_dict()["severity"] == "critical"

    def test_bug_id_pattern(self) -> None:
        with pytest.raises(ValidationError):
            DefectCreate(bug_id="bug 1021", title="x")

    def test_related_cases_dedupe(self) -> None:
        payload = DefectCreate(bug_id="BUG-1", title="x", related_cases=["PAY-001", "PAY-001"])
        assert payload.related_cases == ["PAY-001"]

    def test_related_cases_reject_invalid_business_ids(self) -> None:
        for bad in ("PAY 001", "登录-001", "PAY/001"):
            with pytest.raises(ValidationError):
                DefectCreate(bug_id="BUG-1", title="x", related_cases=[bad])

    def test_update_related_cases_reject_invalid_business_ids(self) -> None:
        with pytest.raises(ValidationError):
            DefectUpdate(related_cases=["PAY 001"])

    def test_update_is_partial(self) -> None:
        assert DefectUpdate(resolution="已修复").to_orm_dict(exclude_unset=True) == {
            "resolution": "已修复"
        }

    def test_read_from_orm(self, db_session) -> None:
        from app.models import Defect

        defect = Defect(bug_id="BUG-9", title="登录超时", severity="major")
        db_session.add(defect)
        db_session.commit()
        db_session.refresh(defect)
        read = DefectRead.model_validate(defect)
        assert read.severity is Severity.MAJOR


class TestRequirementAndDocumentSchemas:
    def test_requirement_create(self) -> None:
        payload = RequirementCreate(req_id="REQ-001", title="登录改版", status="approved")
        assert payload.to_orm_dict()["status"] == "approved"

    def test_requirement_rejects_unknown_status(self) -> None:
        with pytest.raises(ValidationError):
            RequirementCreate(req_id="REQ-001", title="x", status="done")

    def test_document_metadata_defaults_to_empty_dict(self) -> None:
        payload = DocumentCreate(doc_id="DOC-001", title="测试规范")
        assert payload.metadata == {}

    def test_document_read_from_orm(self, db_session) -> None:
        from app.models import Document

        doc = Document(doc_id="DOC-777", title="规范", meta={"a": 1})
        db_session.add(doc)
        db_session.commit()
        db_session.refresh(doc)
        assert DocumentRead.model_validate(doc).metadata == {"a": 1}


class TestPage:
    def test_page_is_generic_and_validates(self) -> None:
        page = Page[TestCaseRead].model_validate(
            {
                "items": [
                    {
                        "id": 1,
                        "case_id": "A-1",
                        "title": "t",
                        "tags": [],
                        "created_at": "2026-10-16T00:00:00Z",
                        "updated_at": "2026-10-16T00:00:00Z",
                    }
                ],
                "total": 1,
                "limit": 20,
                "offset": 0,
            }
        )
        assert page.total == 1
        assert page.items[0].case_id == "A-1"
