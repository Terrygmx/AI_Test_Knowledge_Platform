# Phase 25 Review：Agentic RAG 骨架

- 日期：2026-10-24 14:40（Asia/Shanghai）
- 分支：`phase-12-evaluation-dataset`
- 结论：**工具白名单、Planner 协议与受控循环已实现；真实 LLM Planner 与工具执行器未接入**

## 已实现

- 新增 `app/agent/tools.py`：
  - `ToolRegistry` 白名单：未注册工具返回失败结果而不是抛异常，
    参数错误转换为 `ToolResult.errors`，不让 Planner 拿到堆栈；
  - `ToolCall` 必须携带 `reason`，trace 保留每次选择的理由；
  - `AgentLoop` 强制 `max_steps`，Planner 返回超长计划直接失败，
    防止无限 Agent loop；
  - `DeterministicPlanner` 按关键词路由到
    `search_testcases` / `search_defects` / `search_requirements` /
    `compare_versions`，只用于离线测试与降级。

## 测试

- 新增 `tests/test_agent_tools.py`：未知工具、重复工具名、参数错误转换、
  关键词路由、证据聚合、失败 trace 与超长计划拒绝；
- 全量 SQLite：678 passed, 17 skipped。

## 发现并修复的问题

- 初版超长计划测试用 `*3` 复制调用，在默认 `max_steps=5` 下只有 3 步，
  没有触发上限；已改为 `*(max_steps + 1)` 确保真正越过边界。

## 边界

- 工具只定义协议，`search_testcases` 等尚未绑定真实检索服务；
- `compare_versions` 依赖 Phase 22 的 `VersionDiffRequest`，
  差异呈现语义需人工确认；
- LLM Planner 未实现；接入时必须走与 Phase 15 相同的 JSON 严格校验，
  不能让自由文本直接调用工具。
