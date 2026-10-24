"""Phase 26 智能回归测试 Agent：确定性计划草稿与人工确认门槛。

计划只基于检索到的证据（需求 / 变更用例 / P0-P1 缺陷 / 历史回归），
不调用 LLM 编造步骤。计划草稿必须经过人工确认才能执行，
``approved_by`` / ``approved_at`` 是执行前的硬门槛。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from app.schemas.common import BaseSchema


class RegressionStepKind(StrEnum):
    """回归计划的步骤来源。"""

    REQUIREMENT = "requirement"
    TESTCASE = "testcase"
    DEFECT = "defect"
    HISTORICAL = "historical"


class RegressionStep(BaseSchema):
    """计划中的一步；必须携带证据来源，不能只有动作描述。"""

    kind: RegressionStepKind
    source_type: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    instruction: str = Field(min_length=1)
    priority: str | None = None


class RegressionPlan(BaseSchema):
    """一次回归测试计划草稿。"""

    plan_id: str = Field(min_length=1)
    module: str = Field(min_length=1)
    version: str = Field(min_length=1)
    steps: list[RegressionStep] = Field(default_factory=list)
    status: str = "draft"
    approved_by: str | None = None
    approved_at: datetime | None = None
    generated_from: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_provenance(self) -> RegressionPlan:
        if self.status == "draft":
            if self.approved_by is not None or self.approved_at is not None:
                raise ValueError("draft 计划不能带批准信息")
        elif self.status == "approved":
            if not self.approved_by:
                raise ValueError("approved 计划必须记录 approved_by")
            if self.approved_at is None:
                raise ValueError("approved 计划必须记录 approved_at")
            if self.approved_at.tzinfo is None or self.approved_at.utcoffset() is None:
                raise ValueError("approved_at 必须带时区")
        else:
            raise ValueError("status 必须是 draft 或 approved")
        return self

    def approve(self, *, approved_by: str, approved_at: datetime) -> None:
        """人工确认；这是执行前的硬门槛，不允许跳过。"""
        if approved_by != "Terry":
            raise ValueError("批准人必须是 Terry")
        if approved_at.tzinfo is None or approved_at.utcoffset() is None:
            raise ValueError("approved_at 必须带时区")
        self.status = "approved"
        self.approved_by = approved_by
        self.approved_at = approved_at


def build_regression_plan(
    *,
    plan_id: str,
    module: str,
    version: str,
    requirements: list[dict[str, Any]],
    testcases: list[dict[str, Any]],
    defects: list[dict[str, Any]],
    historical: list[dict[str, Any]],
) -> RegressionPlan:
    """按证据生成计划草稿；无证据的模块生成空计划，由人工决定是否执行。

    输入项统一为 ``{"source_id", "evidence", ...}``，由调用方的 Agent 工具
    从检索结果投影；本函数不做检索，也不改写证据文本。
    """
    steps: list[RegressionStep] = []
    for item in requirements:
        steps.append(
            RegressionStep(
                kind=RegressionStepKind.REQUIREMENT,
                source_type="requirement",
                source_id=item["source_id"],
                evidence=item["evidence"],
                instruction=f"核对需求验收标准：{item['source_id']}",
            )
        )
    for item in testcases:
        steps.append(
            RegressionStep(
                kind=RegressionStepKind.TESTCASE,
                source_type="testcase",
                source_id=item["source_id"],
                evidence=item["evidence"],
                instruction=f"执行变更用例：{item['source_id']}",
                priority=item.get("priority"),
            )
        )
    for item in defects:
        steps.append(
            RegressionStep(
                kind=RegressionStepKind.DEFECT,
                source_type="defect",
                source_id=item["source_id"],
                evidence=item["evidence"],
                instruction=f"回归验证缺陷修复：{item['source_id']}",
                priority=item.get("priority"),
            )
        )
    for item in historical:
        steps.append(
            RegressionStep(
                kind=RegressionStepKind.HISTORICAL,
                source_type=str(item.get("source_type", "testcase")),
                source_id=item["source_id"],
                evidence=item["evidence"],
                instruction=f"复跑历史回归：{item['source_id']}",
            )
        )
    return RegressionPlan(
        plan_id=plan_id,
        module=module,
        version=version,
        steps=steps,
        generated_from={
            "requirements": len(requirements),
            "testcases": len(testcases),
            "defects": len(defects),
            "historical": len(historical),
        },
    )


__all__ = [
    "RegressionPlan",
    "RegressionStep",
    "RegressionStepKind",
    "build_regression_plan",
]
