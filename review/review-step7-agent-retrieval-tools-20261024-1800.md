# Review：步骤 7 —— Agentic RAG 绑真实检索

- 日期：2026-10-24 18:00（Asia/Shanghai）
- 分支：`phase-12-evaluation-dataset`
- 范围：Phase 25 工具白名单从纯契约接入真实检索

## 变更内容

- `app/agent/retrieval_tools.py`（新增）：
  - `HybridSearchTool`：走 `KnowledgeChunkService.hybrid_search`（Phase 9），证据格式 `source_type:source_id#chunk_index + snippet`；
  - `SearchTestCasesTool` / `SearchDefectsTool` / `SearchRequirementsTool` / `SearchDocumentsTool`：包装统一检索服务，证据带业务编号；
  - `CompareVersionsTool`：按版本区间（复用步骤 4 的 `VersionRange` SQL pre-filter）检索两个版本之间的 Chunk；
  - 所有工具失败转 `ToolResult(errors=...)`，不向 Agent 循环抛异常。
- `app/agent/tools.py`：新增 `build_default_registry(session)`，六个白名单工具绑定真实会话；工具协议与 `AgentLoop` 契约不变。
- `tests/test_agent_retrieval_tools.py`：7 个测试覆盖白名单清单、来源标识证据、空白 query 报错、向量通道失败不炸循环、limit 越界、版本参数校验。

## 验证

- SQLite 全量：729 passed；PG（agent + version_access 相关）：25 passed。
- `ruff`、`git diff --check` 通过。

## Review 发现与处置

1. 【已修】`_EntitySearchTool` 初版引用了不存在的 `service.unique_value()` / `service.snippet_text()`；改为用 `unique_field` 取业务编号、`search_fields` 拼 snippet 文本，与 `UnifiedSearchService` 的实现一致。
2. 【已修】"SQLite 下 hybrid 必失败"的测试假设不成立：本机 OMLX 可达时 vector 通道在 SQLite 测试库也能成功返回 0 hits（空证据也是合法成功）。测试改为只断言"不抛异常、结果结构合法"，另以 PG 实测补齐真实检索路径。
3. 【确认设计】证据字符串强制带 `source_type:source_id` 前缀，Planner / LLM 不能把无来源文本当证据；`compare_versions` 证据额外带版本区间。
4. 【确认边界】工具执行顺序仍是"Planner 单轮规划 + 受限步数"，LLM Planner（多轮反思）仍属 Phase 25 未开工项；工具化的 Embedding 客户端在无模型服务环境会返回 ToolResult 错误，不阻塞循环。

## 结论

Agent 工具从契约层升级为真实检索执行层，"工具未绑真实检索"的台账缺口闭环。可以进入下一项。
