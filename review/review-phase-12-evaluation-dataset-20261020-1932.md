# Review：Phase 12 Evaluation Dataset（候选完成）

- 分支：`phase-12-evaluation-dataset`
- 评审时间：2026-10-20 19:32（Asia/Shanghai）
- 当前 HEAD：`df62284384d2262210b8c99a4cb79ade83719900`
- 评审范围：Phase 11 review 修复后的未提交增量，重点是 Phase 12
  Evaluation Dataset 契约、100 条候选、生成 / 校验 CLI 与文档。
- 变更规模：当前工作区包含 Phase 11 修复和 Phase 12 新增文件；
  已跟踪文件 `git diff --stat` 为 11 files changed, 356 insertions(+),
  42 deletions(-)，另有 Phase 12 未跟踪新增资产与测试。

## 验证结果

- `uv run ruff check .`：通过。
- `uv run pytest -q`：571 tests，0 failures / errors，17 skipped
  （PostgreSQL 相关）。
- `git diff --check`：通过。
- `uv run python scripts/phase12_candidates.py --dry-run`：100 条候选，
  分类 30 / 20 / 20 / 10 / 10 / 10，全部 `pending`。
- `uv run python scripts/phase12_candidates.py --check`：通过，数据集
  SHA-256 前缀 `5b77a029aebe`。
- `uv run python scripts/phase12_candidates.py --check --mode formal`：
  按预期失败，提示仍有 100 条未确认，退出码 1。
- 重新生成候选与当前 fixture 除 `generated_at` 外一致。
- `uv run alembic check`：未完成；本机 PostgreSQL
  `127.0.0.1:5432` 连接被拒绝，不能声称迁移检查通过。

## Review 中发现并已修复的问题

### 1. 生成器源码存在重复对象字段，导致候选证据漂移

- 位置：`scripts/phase12_candidates.py:255-263`、
  `eval/fixtures/phase6_simulated.json:146-149`。
- `SHOP-CART-001` 的 TestCase seed 中 `case_id` 字段被重复写入；
  `P12-TC-027` 的候选行中问题文本被误当作证据文本重复。Python 解析
  JSON 后只保留最后一个 `case_id`，所以既有测试没有发现这种格式漂移。
- 修复：删除重复 `case_id`，把 `P12-TC-027` 的证据恢复为
  `不能进入支付页`。
- 防回归：新增
  `tests/test_phase12_dataset.py::test_candidate_generator_payload_has_unique_object_fields`，
  检查生成 payload 不含重复 `case_id` JSON 片段，并检查可回答候选的
  问题与答案要点不同。

## 设计与纪律符合性

| 约束 / 计划项 | 结论 | 依据 |
|---|---|---|
| Phase 12 独立数据契约 | 符合 | `app/evaluation/phase12_dataset.py` 未修改 Phase 6–11 `ChunkingDataset` |
| 六类固定配额 | 符合 | dry-run 与 loader 均校验 30 / 20 / 20 / 10 / 10 / 10 |
| AI 只生成候选 | 符合 | 100 条全部 `pending`；formal 模式拒绝未确认数据 |
| 人工审阅溯源 | 符合 | `confirmed` / `rejected` 必须有审阅人和带时区时间；`pending` 禁止审阅元数据 |
| 来源级证据 | 符合 | 校验来源存在与证据原文；`gold_documents` 支持四类来源 |
| Chunk 级证据 | 符合 | 校验 Chunk 序号、Chunk 正文证据、重复证据 |
| 过滤条件语义 | 符合 | 校验 `expected_filters` 合法且不会过滤掉 Gold Chunk |
| 持久化边界 | 符合 | 当前使用 JSON fixture；未创建 `evaluation_cases` / `evaluation_results` 表，文档已说明 |
| 阶段状态 | 符合 | 文档明确“候选完成，人工 Gold Set 尚未确认”，未宣称正式 Recall / MRR |

## 当前限制与风险

1. 候选集仍不能作为正式 Gold Set 使用；必须逐条人工确认，不能脚本批量改状态。
2. 当前语料只有 25 个结构化来源和 3 篇文档，100 条 Query 中有 20 组同源
   Gold 重复，存在覆盖集中风险。
3. 证据匹配使用规范化子串；这适合实验可追溯，但人工确认时仍需确认语义，
   而不仅是字符串存在。
4. `evaluation_cases` / `evaluation_results` 持久化按计划延后；当前不冒充
   数据库已有评测资产。
5. PostgreSQL 不可用导致 `alembic check` 与 PG 相关 skipped 测试未复验。

## 下一步建议

1. 逐条审阅 100 条候选；确认后填写真实审阅人、带时区时间和必要备注。
2. 运行 `uv run python scripts/phase12_candidates.py --check --mode formal`，
   只有通过后才进入 Phase 13 正式检索评测。
3. PostgreSQL 恢复后重跑 `uv run alembic check` 与 PG 全量测试。
4. 后续如需要在线管理评测集，再设计 `evaluation_cases` / `evaluation_results`
   迁移，避免提前建表造成契约漂移。
