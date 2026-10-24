# Review：Phase 13 Retrieval Evaluation

- 分支：`phase-12-evaluation-dataset`
- 评审时间：2026-10-22 21:20（Asia/Shanghai）
- 当前 HEAD：`df62284384d2262210b8c99a4cb79ade83719900`
- 评审范围：Phase 13 指标、评测运行器、CLI、manifest、报告与测试
- 关键文件：
  - `app/evaluation/metrics.py`
  - `app/evaluation/retrieval_evaluation.py`
  - `scripts/retrieval_evaluation.py`
  - `docs/RETRIEVAL_EVALUATION.md`
  - `results/retrieval-phase13.json`

## 验证结果

- SQLite：`596 passed, 17 skipped`
- PostgreSQL（`aitkp_test`）：`596 passed, 2 skipped`
- PostgreSQL 容器：healthy；PostgreSQL 17.11；pgvector 0.8.7
- `uv run alembic check`：通过，当前 `0002 (head)`
- `uv run ruff check .`：通过
- `git diff --check`：通过
- Phase 13 默认 manifest 可重跑，报告数字与 manifest 一致性测试通过
- Phase 12 候选集 `candidate` 模式可运行；`formal` 模式拒绝 100 条
  `pending` 数据，且不生成结果文件

## 发现并已修复

### 1. NDCG 对重复检索结果重复计 gain，可超过 1

- 位置：`app/evaluation/metrics.py:78`
- 原实现直接遍历 `retrieved[:k]`，同一 Gold 项在结果中重复出现时会重复累加
  `1 / log2(rank + 1)`。实测：

  ```text
  ndcg_at_k(["A", "A"], ["A"], 2) == 1.6309...
  ```

  NDCG 被定义为 DCG/IDCG，按当前二值 Gold 语义应限制在 `[0, 1]`；超过 1
  会让报告和 Badcase 解释失真。
- 修复：每条 Gold 证据最多贡献一次 gain，重复检索项不再重复计分。
- 防回归：新增
  `tests/test_evaluation_metrics.py::test_duplicate_retrieved_items_cannot_inflate_ndcg`，
  同时覆盖重复 Gold 和重复检索结果。

## 发现但未修改

### 2. `formal` 模式本身没有数据审阅状态，依赖调用方先校验

- 位置：`app/evaluation/retrieval_evaluation.py:39`
- `evaluate_retriever(mode="formal")` 只记录模式，不检查 `EvaluationQuery`
  是否来自已人工确认的 Phase 12 数据集。CLI 路径会先调用
  `load_phase12_dataset(..., mode="formal")`，因此当前入口是安全的；但直接调用
  运行器时可以把任意 `ChunkingDataset` 标记为 `formal`。
- 这是接口设计风险，不是当前 CLI 的阻断缺陷。若要收口，应让 formal 运行器
  接收带审阅元数据的数据集或显式 provenance 对象，而不是仅接收布尔式 mode。

### 3. manifest 未记录 `--top-k` 以外的全部结果字段口径

- 位置：`scripts/retrieval_evaluation.py:56`
- manifest 已记录数据集、语料哈希、模式、指标定义与结果哈希，但没有单独
  记录实际使用的 `top_k` 和 `k_values` 到顶层；这些信息目前只在每条结果内。
- 不影响当前报告一致性，但后续多运行汇总时应把运行级参数显式提升到顶层。

### 4. 当前正式结论仍受 Phase 12 人工确认阻塞

- Phase 13 的指标实现和流程已完成；当前报告使用 Phase 8 的 24 条模拟 Query，
  manifest 明确 `mode=candidate`、`formal_gold_set=false`。
- 不能把当前 BM25 的 Recall@5 / NDCG 结果宣称为正式 Gold Set 结论。

## 纪律与设计符合性

| 约束 / 计划项 | 结论 | 依据 |
|---|---|---|
| 自己实现 Recall@K / Precision@K / MRR | 符合 | `metrics.py` 提供独立纯函数与聚合器 |
| 理解 NDCG | 符合（已修复重复计分） | 二值 gain、DCG/IDCG、重复项回归测试 |
| 不依赖评测框架 | 符合 | 纯 Python 实现，无 Ragas 或指标库 |
| 候选 / 正式边界 | 符合 | CLI formal 拒绝 pending；manifest 标记 `formal_gold_set` |
| 结果可追溯 | 符合 | 数据集 / 语料哈希、命令、Git 状态、逐 Query 细节与 `result_sha256` |
| Badcase 归因边界 | 符合 | 只做检索层初筛，未冒充生成错误归因 |
| 小样本结论纪律 | 符合 | 报告明确 24 条 Query、探索性结果与限制 |
| 报告一致性 | 符合 | `tests/test_retrieval_evaluation_report.py` 校验 manifest 数字 |

## 下一步

1. 完成 Phase 12 人工逐条确认，生成正式 Gold Set；
2. 用 formal 模式重跑 ILIKE / BM25 / Vector / Hybrid，并生成正式报告；
3. 如要让运行器自身强制 formal provenance，增加独立已确认数据集类型或
   review receipt，而不是只依赖字符串 `mode`；
4. 后续把运行级 `top_k` / `k_values` 提升到 manifest 顶层，便于跨运行汇总。
