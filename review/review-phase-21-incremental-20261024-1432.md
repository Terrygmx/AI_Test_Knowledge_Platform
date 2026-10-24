# Phase 21 Review：增量索引

- 日期：2026-10-24 14:32（Asia/Shanghai）
- 分支：`phase-12-evaluation-dataset`
- 结论：**hash 对比与变更计划已实现；执行器与 CLI 尚未接入**

## 已实现

- 新增 `app/ingestion/incremental.py`：
  - `SourceState.content_hash` 复用既有 `content_sha256`，与
    `documents.content_hash` / `knowledge_chunks.content_hash` 同一算法；
  - `plan_incremental_index()` 对比期望状态与库内 hash，生成
    CREATE / UPDATE / DELETE / UNCHANGED 计划；
  - 期望状态中不存在的库内来源生成 DELETE，避免旧数据残留；
  - 只有 CREATE / UPDATE 需要 Embedding，DELETE 不触发模型调用。

## 测试

- 新增 `tests/test_incremental_index.py`：新增 / 修改 / 未变 / 删除、
  hash 稳定性与内容敏感性、空期望状态全量删除；
- 全量 SQLite：658 passed, 17 skipped。

## 边界

- 计划与执行解耦：调用方需要把 `pending_changes` 接到既有
  `DocumentService` / `KnowledgeChunkService` 才会真正写入；
- 删除计划只标记来源级实体；同一来源的部分 Chunk 变化仍依赖
  Chunker 重新切分后按 Chunk hash 对比，当前按来源粒度判断；
- 未提供 CLI 入口，批量同步需等执行器接入后统一实现。
