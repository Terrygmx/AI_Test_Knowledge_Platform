"""Phase 25 Agentic RAG：受控工具集与 Agent 循环契约。

工具是唯一允许产生证据的入口；Planner 只能选择工具和参数，
不能直接生成答案或编造来源。循环最大轮次强制受限，防止无限 Agent loop。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from pydantic import Field

from app.schemas.common import BaseSchema


class ToolCall(BaseSchema):
    """Planner 输出的工具调用。"""

    tool: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1)


class ToolResult(BaseSchema):
    """工具执行结果；errors 为空才算成功。"""

    tool: str
    evidence: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class AgentTool(Protocol):
    """可注册的工具协议；name 是 Planner 的选择键。"""

    name: str

    def run(self, arguments: dict[str, Any]) -> ToolResult: ...


class ToolRegistry:
    """白名单工具集合；未注册工具拒绝执行。"""

    def __init__(self, tools: Sequence[AgentTool]) -> None:
        self._tools: dict[str, AgentTool] = {}
        for tool in tools:
            if tool.name in self._tools:
                raise ValueError(f"工具名重复：{tool.name}")
            self._tools[tool.name] = tool

    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    def run(self, call: ToolCall) -> ToolResult:
        tool = self._tools.get(call.tool)
        if tool is None:
            return ToolResult(
                tool=call.tool,
                errors=[f"未知工具：{call.tool}；可用工具：{', '.join(self.names)}"],
            )
        try:
            return tool.run(call.arguments)
        except (KeyError, TypeError, ValueError) as exc:
            return ToolResult(tool=call.tool, errors=[str(exc)])


class AgentPlanner(Protocol):
    """把问题映射成最多 max_steps 步工具调用。"""

    name: str

    def plan(self, question: str, *, max_steps: int) -> list[ToolCall]: ...


class DeterministicPlanner:
    """基于关键词的规则 Planner；只在离线测试与降级路径使用。"""

    name = "deterministic"

    def plan(self, question: str, *, max_steps: int) -> list[ToolCall]:
        if max_steps < 1:
            raise ValueError("max_steps 必须 >= 1")
        lowered = question.casefold()
        if "缺陷" in lowered or "bug" in lowered:
            tool = "search_defects"
        elif "需求" in lowered:
            tool = "search_requirements"
        elif "对比" in lowered or "变化" in lowered:
            tool = "compare_versions"
        else:
            tool = "search_testcases"
        return [ToolCall(tool=tool, arguments={"query": question}, reason="关键词路由")]


class AgentLoop:
    """Planner → Tool → Evidence 的受控循环。"""

    def __init__(self, *, registry: ToolRegistry, planner: AgentPlanner, max_steps: int = 5) -> None:
        if max_steps < 1:
            raise ValueError("max_steps 必须 >= 1")
        self._registry = registry
        self._planner = planner
        self._max_steps = max_steps

    def run(self, question: str) -> dict[str, Any]:
        calls = self._planner.plan(question, max_steps=self._max_steps)
        if len(calls) > self._max_steps:
            raise ValueError("Planner 步数超过 max_steps")
        evidence: list[str] = []
        trace: list[dict[str, Any]] = []
        for call in calls:
            result = self._registry.run(call)
            trace.append(
                {
                    "tool": call.tool,
                    "reason": call.reason,
                    "ok": result.ok,
                    "errors": result.errors,
                }
            )
            if result.ok:
                evidence.extend(result.evidence)
        return {"question": question, "evidence": evidence, "trace": trace}


__all__ = [
    "AgentLoop",
    "AgentPlanner",
    "AgentTool",
    "DeterministicPlanner",
    "ToolCall",
    "ToolRegistry",
    "ToolResult",
]
