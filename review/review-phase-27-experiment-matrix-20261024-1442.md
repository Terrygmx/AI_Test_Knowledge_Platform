# Phase 27 Review：最终实验矩阵

- 日期：2026-10-24 14:42（Asia/Shanghai）
- 分支：`phase-12-evaluation-dataset`
- 结论：**矩阵聚合与缺失行标记已实现；正式格子必须等人工 Gold Set 重跑**

## 已实现

- 新增 `app/evaluation/experiment_matrix.py`：
  - 从 Phase 13 / 9 / 8 / 16 的 manifest 提取 Recall@5 / MRR / P95，
    生成统一对比行；
  - 每行携带 `source_phase` / `mode` / `formal_gold_set` 溯源；
  - 计划中的 vector / hybrid_rerank / final 行在数据缺失时显式进入
    `missing_experiments`，不用旧数字或估算填充；
  - note 明确所有当前数字都是 candidate 模式。

## 测试

- 新增 `tests/test_experiment_matrix.py`：聚合现有三个 manifest、
  缺失行标记、空输入拒绝与 candidate 边界注记；
- 全量 SQLite：685 passed, 17 skipped。

## 边界

- Vector / Rerank 行依赖 Phase 9 / 11 旧实验 manifest，正式重跑前
  不填入矩阵；Phase 11 reranker 仍不可用；
- `final` 行按计划由人工评定，不自动生成；
- 矩阵只做数字聚合，不重跑实验；实验执行仍由各阶段脚本负责。
