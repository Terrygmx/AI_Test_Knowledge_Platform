# Phase 17 Review：Citation 增强

- 日期：2026-10-24 14:22（Asia/Shanghai）
- 分支：`phase-12-evaluation-dataset`
- 结论：**核心缺口已修复并通过全量测试；展示层来源清单为服务端契约，未接入前端**

## 已实现

- `Citation` 新增 `chunk_id` 与 `score`，对齐计划中的 Evidence 字段；
  `chunk_id` 为 `knowledge_chunks` 主键，离线实验可为空。
- `assemble_context()` 把 Chunk 主键与 cosine distance 写入 Citation；
  `score` 当前与 vector distance 同值，后续接入 Hybrid / Rerank 时记录各通道原始分。
- `RAGResponse.source_lines()` 按引用顺序输出去重后的来源清单，
  例如 `testcase:LOGIN-001`，用于回答后的“来源”区块。

## 测试与校验

- `tests/test_rag.py`：验证 `chunk_id` / `score` / `source_lines()`；
- 全量 SQLite：639 passed, 17 skipped；
- `ruff check .` 通过。

## 边界

- API 响应尚未新增独立“来源”字段；调用方可用 `source_lines()` 组装；
- `score` 语义与 `retriever` 相关，跨检索器比较分数没有意义；
- Citation 正确性（编号是否指向真正支撑答案的片段）仍由 Phase 14 评测器
  离线判定，运行时 `citation_valid` 只表示编号合法。
