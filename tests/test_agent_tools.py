"""Phase 25 Agent 工具白名单、Planner 与循环测试。"""

from __future__ import annotations

import pytest

from app.agent.tools import (
    AgentLoop,
    DeterministicPlanner,
    ToolCall,
    ToolRegistry,
    ToolResult,
)


class FakeTool:
    def __init__(self, name: str, evidence: list[str] | None = None, error: str | None = None) -> None:
        self.name = name
        self._evidence = evidence or []
        self._error = error
        self.calls: list[dict] = []

    def run(self, arguments: dict) -> ToolResult:
        self.calls.append(arguments)
        if self._error:
            return ToolResult(tool=self.name, errors=[self._error])
        return ToolResult(tool=self.name, evidence=list(self._evidence))


def test_registry_rejects_unknown_tools_without_raising() -> None:
    registry = ToolRegistry([FakeTool("search_testcases", ["TC-1"])])

    result = registry.run(ToolCall(tool="search_defects", reason="x"))

    assert result.ok is False
    assert "未知工具" in result.errors[0]


def test_registry_rejects_duplicate_tool_names() -> None:
    with pytest.raises(ValueError, match="重复"):
        ToolRegistry([FakeTool("same"), FakeTool("same")])


def test_registry_converts_argument_errors_to_failed_result() -> None:
    class StrictTool(FakeTool):
        def run(self, arguments: dict) -> ToolResult:
            if "query" not in arguments:
                raise ValueError("缺少 query")
            return super().run(arguments)

    registry = ToolRegistry([StrictTool("search_testcases")])

    result = registry.run(ToolCall(tool="search_testcases", reason="x"))

    assert result.ok is False
    assert "缺少 query" in result.errors[0]


def test_deterministic_planner_routes_by_keywords() -> None:
    planner = DeterministicPlanner()

    assert planner.plan("支付缺陷的根因", max_steps=3)[0].tool == "search_defects"
    assert planner.plan("退款需求是什么", max_steps=3)[0].tool == "search_requirements"
    assert planner.plan("v2.3 和 v2.4 的变化", max_steps=3)[0].tool == "compare_versions"
    assert planner.plan("登录用例", max_steps=3)[0].tool == "search_testcases"
    with pytest.raises(ValueError, match="max_steps"):
        planner.plan("x", max_steps=0)


def test_agent_loop_collects_evidence_and_trace() -> None:
    tool = FakeTool("search_testcases", ["TC-1"])
    loop = AgentLoop(registry=ToolRegistry([tool]), planner=DeterministicPlanner())

    outcome = loop.run("登录用例怎么写")

    assert outcome["evidence"] == ["TC-1"]
    assert outcome["trace"][0]["tool"] == "search_testcases"
    assert outcome["trace"][0]["ok"] is True


def test_agent_loop_records_failed_tool_without_evidence() -> None:
    tool = FakeTool("search_defects", error="索引为空")
    loop = AgentLoop(registry=ToolRegistry([tool]), planner=DeterministicPlanner())

    outcome = loop.run("缺陷根因")

    assert outcome["evidence"] == []
    assert outcome["trace"][0]["ok"] is False


def test_agent_loop_rejects_overlong_plan() -> None:
    class OverlongPlanner:
        name = "overlong"

        def __init__(self) -> None:
            self._base = DeterministicPlanner()

        def plan(self, question: str, *, max_steps: int) -> list:
            return self._base.plan(question, max_steps=max_steps) * (max_steps + 1)

    loop = AgentLoop(
        registry=ToolRegistry([FakeTool("search_testcases")]),
        planner=OverlongPlanner(),
    )

    with pytest.raises(ValueError, match="max_steps"):
        loop.run("登录用例")
