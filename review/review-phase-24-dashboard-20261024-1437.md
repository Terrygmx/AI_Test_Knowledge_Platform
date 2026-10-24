# Phase 24 Review：Dashboard 数据聚合

- 日期：2026-10-24 14:37（Asia/Shanghai）
- 分支：`phase-12-evaluation-dataset`
- 结论：**manifest 聚合契约已实现；未做 UI，未暴露 HTTP 端点**

## 已实现

- 新增 `app/evaluation/dashboard.py`：
  - `retrieval_summary()` 提取 Phase 13 各检索器指标；
  - `generation_summary()` 提取 Phase 14 生成指标；
  - `dataset_summary()` 提取 Phase 15 候选审阅状态与理解覆盖；
  - 每个块都保留 `phase` / `mode` / `formal_gold_set` 溯源字段，
    前端展示时能直接区分 candidate 与 formal；
  - `build_dashboard()` 支持部分聚合，至少要求一个 manifest。

## 测试

- 新增 `tests/test_dashboard.py`：三块聚合、部分聚合、空输入拒绝，
  直接读取仓库现有 manifest 保证字段路径不漂移；
- 全量 SQLite：671 passed, 17 skipped。

## 边界

- 计划中的 Token Usage / Top Questions / P95 需要 API 日志与性能数据
  持久化后才可聚合；当前只聚合已有评测 manifest；
- 没有 HTTP 端点；接入 FastAPI 时应只读 `results/` 或数据库快照，
  不在请求路径上重算评测；
- UI 按“不花大量时间做 UI”的计划约束暂不实现。
