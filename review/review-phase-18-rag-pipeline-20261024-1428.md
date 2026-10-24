# Phase 18 Review：完整 RAG Pipeline

- 日期：2026-10-24 14:28（Asia/Shanghai）
- 分支：`phase-12-evaluation-dataset`
- 结论：**Pipeline 与逐阶段 trace 已实现并通过全量测试；未接入 HTTP API 与真实 Rerank**

## 已实现

- 新增 `app/generation/pipeline.py`：Understanding → Query Rewrite →
  Embedding → Vector / BM25 → Fusion → 可选 Rerank → Evidence → LLM → Citation。
- `RAGResponse` 新增 `trace` 字段，记录原始 Query、结构化理解、过滤条件、
  各通道 hits、融合 / 重排结果、最终上下文、各阶段耗时和 rewrite 降级原因。
- 混合检索离线路径复用 `fuse()`；SQLite 下用精确 cosine distance 与 BM25
  候选，不伪装成 pgvector 结果；PostgreSQL 接缝仍由既有 service 提供。
- 过滤条件来自 Query Understanding 投影，语义与 `matches_metadata` 一致，
  不满足条件的候选在进入融合前就被排除。

## 测试

- 新增 `tests/test_rag_pipeline.py`：引用回答、过滤器传递、过滤饥饿拒答、
  未引用降级、越界引用失败、空白 Query 与非法 `top_k`；
- 全量 SQLite：645 passed, 17 skipped；
- `ruff check .` 通过。

## 发现并修复的问题

- 初版直接调用 `hybrid_search()` / `search()`，在 SQLite 测试中触发
  “仅支持 PostgreSQL”错误；已改为 pipeline 内组合通道候选并在离线路径排序。
- `_offline_keyword_results` 曾用 `(source_type, source_id, 0)` 反查 Chunk，
  会把非 0 序号映射错误；已改为按文档索引精确回查。

## 边界

- `RerankClient` 已接入构造参数，但默认不启用；真实 reranker 仍受
  Phase 11 的服务可用性约束。
- trace 是进程内结构化数据，尚未持久化到数据库或日志存储；Phase 19 的
  Badcase 系统需要决定 trace 的存储位置。
- Pipeline 未暴露 HTTP 端点；现有 `/api/v1/rag/answer` 仍走 Phase 3 最小 RAG。
