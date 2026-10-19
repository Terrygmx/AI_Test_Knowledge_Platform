"""Phase 4 验收：业务实体与检索表示的契约。"""

from __future__ import annotations

from app.models import Defect, TestCase
from app.services.index_service import case_to_chunk, defect_to_chunk


class TestEntityToChunkContract:
    def test_testcase_keeps_all_filter_metadata(self) -> None:
        case = TestCase(
            case_id="LOGIN-P4-001",
            title="短信登录冒烟",
            module="login",
            sub_module="sms",
            version="v1.3",
            priority="P0",
            case_type="smoke",
            tags=["冒烟", "登录"],
        )

        chunk = case_to_chunk(case)

        assert chunk.source_type == "testcase"
        assert chunk.source_id == case.case_id
        assert chunk.version == "v1.3"
        assert chunk.metadata == {
            "module": "login",
            "sub_module": "sms",
            "version": "v1.3",
            "priority": "P0",
            "case_type": "smoke",
            "tags": ["冒烟", "登录"],
        }

    def test_defect_keeps_all_filter_metadata_and_related_cases(self) -> None:
        defect = Defect(
            bug_id="BUG-P4-001",
            title="支付回调失败",
            module="payment",
            version="v1.3",
            severity="critical",
            priority="P0",
            status="fixed",
            related_cases=["PAY-P4-001", "PAY-P4-002"],
        )

        chunk = defect_to_chunk(defect)

        assert chunk.source_type == "defect"
        assert chunk.source_id == defect.bug_id
        assert chunk.version == "v1.3"
        assert chunk.metadata == {
            "module": "payment",
            "version": "v1.3",
            "priority": "P0",
            "severity": "critical",
            "status": "fixed",
            "related_cases": ["PAY-P4-001", "PAY-P4-002"],
        }

    def test_chunk_content_is_retrieval_representation_not_entity_copy(self) -> None:
        case = TestCase(
            case_id="LOGIN-P4-002",
            title="登录失败提示",
            precondition="用户已注册",
            steps="输入错误密码",
            expected_result="提示错误并保留输入",
        )

        chunk = case_to_chunk(case)

        assert "用例编号：LOGIN-P4-002" in chunk.content
        assert "预期结果：提示错误并保留输入" in chunk.content
        assert "用户已注册" in chunk.content
        assert chunk.metadata["module"] is None
