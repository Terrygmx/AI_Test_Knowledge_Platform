# Review：Phase 14 Generation Evaluation

- 分支：`phase-12-evaluation-dataset`
- 评审时间：2026-10-22 22:27（Asia/Shanghai）
- 当前 HEAD：`df62284384d2262210b8c99a4cb79ade83719900`
- 评审范围：Phase 14 生成评测核心、离线运行器、模拟响应夹具、manifest、
  报告与测试
- 关键文件：
  - `app/evaluation/generation_evaluation.py`
  - `scripts/generation_evaluation.py`
  - `eval/fixtures/phase14_simulated_responses.json`
  - `docs/GENERATION_EVALUATION.md`
  - `results/generation-phase14.json`
  - `tests/test_generation_evaluation.py`
  - `tests/test_generation_evaluation_report.py`

## Findings（按严重度）

### P1：拒答词按子串命中，会把事实句或混合回答错误归类为拒答

- 位置：`app/evaluation/generation_evaluation.py:139-155`、
  `app/evaluation/generation_evaluation.py:183-185`、
  `app/evaluation/generation_evaluation.py:317-320`、
  `app/evaluation/generation_evaluation.py:423-425`
- `_is_refusal()` 只要 Answer 任意位置出现“证据不足 / 无法确认”等标记，就把
  整条 Answer 判为拒答；`_context_supported()` 又直接把这些句子视为受支持。
- 已复现：上下文为“支付页面报错不等于扣款失败。”，Answer 为
  “系统无法确认支付结果，因此会自动退款。[1]”时，指标返回
  `faithfulness=1.0`、`unsupported_sentence_count=0`，Badcase 为
  `generation_error_refusal`。这是一个包含无依据事实的幻觉回答，不是纯拒答。
- 相邻问题：已在 `app/generation/rag.py:89-117` 支持“部分回答 + 证据不足”的
  混合输出，但 Phase 14 会把这类回答整体判成误拒答，和下游 RAG 契约不一致。
- 建议：把拒答识别收紧为“纯拒答句 / 整条回答只有拒答”；含事实断言和拒答词
  的句子不能继承拒答豁免。补充“混合拒答”“拒答词幻觉”两个回归测试。

### P1：`reference_answer` 命中后会把所有 `expected_answer_points` 都标记为已覆盖

- 位置：`app/evaluation/generation_evaluation.py:277-296`
- 当提供 `reference_answer` 且它和 Answer 达到字符重合阈值时，代码直接执行
  `answer_point_hits = list(expected_answer_points)`，而不是逐条验证每个答案要点。
- 已复现：`expected_answer_points=["要点一", "要点二"]`、
  `reference_answer="要点一"`、Answer 只有“要点一。”时，
  `answer_coverage=1.0`，且检索和引用都正确时 `badcase=None`。
- 这会让“检索正确但 Answer 遗漏一个要点”的典型 Badcase 被误判为通过。
- 建议：`reference_answer` 只能作为单条语义别名参与匹配，不能覆盖多个独立
  要点的覆盖计数；多要点样本必须逐点匹配。

### P1：Context Recall / Precision 只看 Chunk 身份，不验证上下文正文是否包含 Gold 证据

- 位置：`app/evaluation/generation_evaluation.py:229-247`、
  `app/evaluation/generation_evaluation.py:300-302`
- 当 `context_sources` 中存在 Gold Chunk 身份时，`_context_recall_by_source()`
  直接返回 1.0，并覆盖前面基于正文的证据覆盖结果；`_context_precision()` 也只
  比较身份，不比较内容。
- 已复现：Gold 为 `("document", "D", 0)`，上下文正文为
  `完全无关的上下文。`，但 `context_sources` 仍然传
  `("document", "D", 0)` 时，`context_recall=1.0`、
  `context_precision=1.0`，不会产生 `retrieval_error`。
- 在正常运行器中上下文由语料重建，当前夹具路径不容易触发；但公共评测器契约
  允许任意 `contexts` 与 `context_sources` 组合，这会造成检索错误假阴性，也会
  把不相关上下文中的答案误归因为生成问题。
- 建议：把 Gold 证据片段纳入 `GenerationCase` / evaluator 契约，同时校验身份
  与正文证据；至少不能让身份命中覆盖正文证据缺失。

### P2：`formal` 核心 API 允许单条 confirmed 样本通过，完整数据集覆盖只在 CLI 中强制

- 位置：`app/evaluation/generation_evaluation.py:454-481`、
  `tests/test_generation_evaluation.py:247-263`、
  `scripts/generation_evaluation.py:157-162`
- `evaluate_generation(mode="formal")` 只检查传入的 cases 是否全部为
  `confirmed`；测试甚至明确让单条 confirmed case 通过并返回
  `formal_gold_set=True`。CLI 的 `build_cases()` 另有“必须覆盖全部 Query”的
  检查，但直接调用核心 API 可以绕过。
- 这不是当前 CLI 的数据泄露或错误产物问题，而是 formal provenance 接口风险。
- 建议：formal 返回值不要让调用方仅凭 `mode` 推断完整 Gold Set；应传入并校验
  数据集版本 / 预期 query 数量，或要求显式 review receipt。

### P2：Answer Relevancy 的中文问题词项覆盖几乎不可用

- 位置：`app/evaluation/generation_evaluation.py:113-115`、
  `app/evaluation/generation_evaluation.py:322-328`
- `_TOKEN_SPLIT` 只在空白、标点和括号处切分，中文问句通常被当成一个完整 token。
  正确的中文改写答案经常与问题没有整串 token 交集，relevance 退化为 0；当前
  `answer_relevancy` 往往只是在反映 answer point coverage。
- 文档已声明这是启发式、不是 Ragas 等价指标，因此这是可接受的学习型基线，
  但报告不应把它与 Ragas Answer Relevancy 并列解读。
- 建议：将指标命名标为 `answer_relevancy_heuristic`，或补充字符 n-gram /
  中文分词版本，并在报告中明确区分“回归信号”和“正式生成质量指标”。

### P3：`unsupported_sentence_count` 聚合后是平均值，不是计数

- 位置：`app/evaluation/generation_evaluation.py:352-369`、
  `app/evaluation/generation_evaluation.py:492-501`
- 单样本值是整数句数，但 `_aggregate()` 对它执行 `sum / len`，manifest 中
  得到 `0.2`，字段名 `unsupported_sentence_count` 容易被读成总句数。
- 建议：拆成 `unsupported_sentence_total` 和 `unsupported_sentence_rate`，
  避免汇总指标口径混淆。

## 验证结果

- SQLite 全量：`619 passed, 17 skipped`
- PostgreSQL（`aitkp_test`）全量：`619 passed, 2 skipped`
- `uv run ruff check .`：通过
- `git diff --check`：通过
- `uv run alembic check`：通过，无新迁移操作
- `uv run python scripts/generation_evaluation.py --dry-run`：通过，
  `cases=10`、`mode=candidate`、`sha256=52ca6c8fb3a9`
- `uv run python scripts/generation_evaluation.py --mode formal`：按预期拒绝
  100 条 pending 数据，未生成 formal 产物
- 当前 `results/generation-phase14.json` 为 `candidate` + `simulated`：
  10 条样本、5 个 Badcase；报告数字与 manifest 一致性测试通过

## 纪律与设计符合性

| 约束 / 计划项 | 结论 | 依据 |
|---|---|---|
| 不伪造人工 Gold Set | 符合 | 10 条样本全部 `pending`，manifest `formal_gold_set=false` |
| 模拟响应不可冒充真实模型结果 | 符合 | `response_source=simulated`，报告明确限制 |
| 区分 Retrieval Error / Generation Error | 基本符合 | 已实现归因，但存在上述拒答和身份假阴性边界 |
| 不依赖 Ragas 才能运行 | 符合 | 默认 deterministic evaluator，Ragas 为可选占位层 |
| Citation Accuracy | 基本符合 | 校验编号、对应 Citation 与 context source；需补更强混合回答测试 |
| 正式 / 候选模式 | 符合 CLI 路径 | CLI 拒绝 pending；核心 API 仍有 P2 风险 |
| 可复现实验 | 符合 | 数据/响应哈希、命令、Git 状态、逐样本指标与 Badcase 已写入 manifest |
| 纪律性结论 | 部分符合 | 不应在当前 P1 边界未修复前，把 `Faithfulness` / Badcase 结果用于正式质量判断 |

## Review 结论

Phase 14 的工程结构、manifest 可追溯性和候选 / 模拟响应纪律基本符合计划，
当前默认 CLI 不会伪造正式 Gold Set。但评测核心仍有三个 P1 级正确性边界：
拒答词污染事实句、参考答案错误覆盖多个要点、Chunk 身份绕过正文证据校验。
在这些问题修复并补回归测试前，`results/generation-phase14.json` 只适合作为
工具链 smoke 结果，不能用于真实模型质量评价或正式 Badcase 结论。

## 建议修复顺序

1. 收紧拒答识别与 Faithfulness 豁免，补混合回答 / 拒答词幻觉测试。
2. 修复 `reference_answer` 与多答案要点的覆盖计数。
3. 为 Gold 证据和上下文正文建立可验证契约，避免身份命中假阳性。
4. 收口 formal 核心 API 的完整数据集 provenance。
5. 明确 Answer Relevancy 与 `unsupported_sentence_count` 的指标命名和聚合口径。
