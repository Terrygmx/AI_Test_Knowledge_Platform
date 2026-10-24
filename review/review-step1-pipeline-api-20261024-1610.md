# Review：步骤 1 —— Pipeline 接入 HTTP API

- 日期：2026-10-24 16:10（Asia/Shanghai）
- 分支：`phase-12-evaluation-dataset`
- 范围：Phase 18 RAG Pipeline 的可选 HTTP 入口（`/api/v1/rag/answer/pipeline`）

## 变更内容

- `app/api/rag.py`
  - 新增 `_run_pipeline_answer()`：使用确定性 Query Understanding + NoOp Rewriter 组装 `RAGPipeline`，不替换 `/rag/answer` 默认路径。
  - 把原 `/answer` 端点的并发闸门 + 超时骨架抽成 `_execute_with_guards()`，两个端点共享同一契约：满载 429（`rag_overloaded`）、超时 503、后台线程自管会话、名额在结束时释放。
  - 新增 `POST /rag/answer/pipeline`，响应体带 Phase 18 逐阶段 trace。
- `tests/api/test_rag_api.py`
  - Pipeline 端点返回 citation + trace（understanding / query_embedding / hybrid / context / llm 阶段齐全）。
  - Pipeline 端点复用超时契约（0.01s 超时立即 503）。
  - Pipeline 端点复用满载契约（恒满闸门 → 429 `rag_overloaded`）。

## 验证

- `uv run ruff check .` 通过。
- `uv run pytest -o addopts='' -q tests/test_rag_pipeline.py tests/api`：98 passed, 1 skipped。
- `git diff --check` 通过。

## Review 发现与处置

1. 【已修】`_execute_with_guards()` 的 `type: ignore` 错误码写成 `return-type`，正确码是 `return-value`；无效 ignore 会让 `holder["value"]` 的真实类型错误逃过 mypy。已改为正确码。
2. 【确认设计】Pipeline 端点当前固定 Deterministic Understanding + NoOp Rewriter，这是刻意的最小接入；LLM 版本 understanding / rewrite 的替换属于 Phase 15/16 正式实验后的人工决策，不在本步骤范围。
3. 【确认边界】Pipeline 的检索路径是离线 / SQLite 兼容的内存过滤 + 精确 cosine 排序，不宣称 ANN 生产性能；这与 Phase 18 的既有口径一致，端点文档已写明“实验性慢接口”。
4. 【遗留】trace 仍是响应内嵌返回，未持久化；与 `docs/PHASE_PROGRESS.md` Phase 18 的“trace 未持久化”缺口一致，由后续步骤决定是否落库。

## 结论

实现符合既有并发 / 超时 / 会话契约，未改变 `/rag/answer` 行为；发现并修复 1 处注释级问题。可以进入下一项。
