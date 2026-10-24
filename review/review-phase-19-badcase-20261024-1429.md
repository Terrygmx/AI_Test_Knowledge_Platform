# Phase 19 Review：Badcase 系统

- 日期：2026-10-24 14:29（Asia/Shanghai）
- 分支：`phase-12-evaluation-dataset`
- 结论：**统一分类与闭环契约已实现；持久化存储与自动归因尚未接入**

## 已实现

- 新增 `app/evaluation/badcase.py`：
  - `BadcaseStage` 覆盖 Parser / Chunk / Embedding / Query / Retrieval /
    Rerank / Context / Generation / Citation 九个阶段；
  - `Badcase` 强制绑定至少一条回归 Query，`root_cause` 与 `hypothesis`
    不能为空；
  - 闭环状态 `open → fixing → fixed → verified` 只能顺序推进，
    `verified` 必须附带修复后的 `evaluation_result`；
  - `BadcaseStore` 提供进程内去重、过滤与未闭环计数。

## 测试

- 新增 `tests/test_badcase.py`：回归绑定、闭环顺序、verified 门槛、
  重复 ID 与过滤；
- 全量 SQLite：649 passed, 17 skipped。

## 边界

- Badcase 仍是进程内对象，未持久化；`evaluation_cases` 表尚未创建，
  Phase 12 契约明确持久化推迟；
- Phase 13 / 14 的 `badcase` 字符串（`retrieval_error`、
  `generation_error_citation` 等）尚不能自动映射成 `Badcase` 记录；
  根因与假设必须人工填写，符合“根因不能靠脚本伪造”的约束；
- 多人协作时 `BadcaseStore` 无并发控制，只适合单机回归脚本。
