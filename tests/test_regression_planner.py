"""Phase 26 回归计划生成与人工确认门槛测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.agent.regression_planner import RegressionStepKind, build_regression_plan


def test_plan_builds_steps_from_all_evidence_sources() -> None:
    plan = build_regression_plan(
        plan_id="PLAN-001",
        module="payment",
        version="v2.4",
        requirements=[{"source_id": "REQ-1", "evidence": "支付幂等要求"}],
        testcases=[
            {"source_id": "TC-1", "evidence": "支付超时重试", "priority": "P0"}
        ],
        defects=[{"source_id": "BUG-1", "evidence": "重复扣款", "priority": "P0"}],
        historical=[{"source_id": "TC-9", "evidence": "历史回归集"}],
    )

    assert plan.status == "draft"
    assert [step.kind for step in plan.steps] == [
        RegressionStepKind.REQUIREMENT,
        RegressionStepKind.TESTCASE,
        RegressionStepKind.DEFECT,
        RegressionStepKind.HISTORICAL,
    ]
    assert plan.steps[1].priority == "P0"
    assert plan.generated_from == {
        "requirements": 1,
        "testcases": 1,
        "defects": 1,
        "historical": 1,
    }


def test_plan_can_be_empty_for_uncovered_module() -> None:
    plan = build_regression_plan(
        plan_id="PLAN-002",
        module="marketing",
        version="v2.4",
        requirements=[],
        testcases=[],
        defects=[],
        historical=[],
    )

    assert plan.steps == []
    assert plan.status == "draft"


def test_approval_requires_terry_and_timezone() -> None:
    plan = build_regression_plan(
        plan_id="PLAN-003",
        module="payment",
        version="v2.4",
        requirements=[],
        testcases=[{"source_id": "TC-1", "evidence": "e"}],
        defects=[],
        historical=[],
    )

    with pytest.raises(ValueError, match="Terry"):
        plan.approve(approved_by="someone", approved_at=datetime.now(UTC))
    with pytest.raises(ValueError, match="时区"):
        plan.approve(approved_by="Terry", approved_at=datetime(2026, 10, 24))

    plan.approve(approved_by="Terry", approved_at=datetime.now(UTC))
    assert plan.status == "approved"
    assert plan.approved_by == "Terry"


def test_draft_rejects_approval_metadata() -> None:
    from app.agent.regression_planner import RegressionPlan

    with pytest.raises(ValueError, match="draft"):
        RegressionPlan.model_validate(
            {
                "plan_id": "PLAN-004",
                "module": "payment",
                "version": "v2.4",
                "steps": [],
                "status": "draft",
                "approved_by": "Terry",
            }
        )
