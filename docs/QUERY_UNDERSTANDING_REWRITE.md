# Phase 15/16：Query Understanding 与 Query Rewrite

- 日期：2026-10-23
- 分支：`phase-12-evaluation-dataset`
- 状态：**Schema、确定性基线与候选评测已完成；LLM 改写收益尚未正式验证**
- 数据集：`eval/fixtures/phase8_simulated.json`（24 条模拟 Query）
- 数据集 SHA-256：`be85ce63b760207368bf229a6484235a430360aee82d82ca81e45d42c0f186a1`
- 原始 manifest：`results/query-rewrite-phase16.json`
- Phase 15 候选报告：`results/query-understanding-phase12.json`
- Phase 12 候选 SHA-256：`5b77a029aebea56ca516a35bc0bb4449b168bafbe0ce99336dce540b4fb3752e`

## 1. 目标与边界

Phase 15 把自然语言 Query 转成结构化字段：

```json
{
  "intent": "testcase_search",
  "module": "login",
  "priority": "P0",
  "version": "v1.3",
  "keywords": ["登录", "用例"],
  "semantic_query": "登录模块 P0 v1.3 的短信登录冒烟用例有哪些？"
}
```

Phase 16 比较原始 Query 与 Rewrite 后的 Search Query：

```text
Original Query
→ Query Rewrite
→ Search Query
→ Retrieval
```

当前实现刻意先提供确定性规则，不把 LLM 作为默认依赖。原因：

1. Schema 和业务语义还没有人工确认；
2. 当前没有正式 Gold Set，无法证明 Rewrite 有稳定收益；
3. LLM Structured Output 失败时的降级路径必须可测，不能静默把非法 JSON
   当成合法过滤条件。

## 2. Query Understanding 契约

核心文件：`app/query/understanding.py`。

| 字段 | 约束 | 用途 |
|---|---|---|
| `intent` | 受控枚举 | 选择测试用例 / 缺陷 / 文档 / 需求检索 |
| `module` | 可选字符串 | 投影为 Metadata Filter |
| `priority` | 可选 `P0`–`P3` | 投影为 Metadata Filter |
| `version` | 可选字符串 | 投影为 Metadata Filter |
| `keywords` | 去重、非空 | 解释词项与后续 BM25 输入 |
| `semantic_query` | 非空 | 保留原问题，避免中文 n-gram 碎片降低向量召回 |
| `filters` | 仅 Metadata Filter 白名单 | 显式过滤条件 |
| `accepted_by` | 非空 | 标记确定性 / LLM / 重试后的接受路径 |

`to_metadata_filters()` 只投影 `module` / `priority` / `version`，并经过现有
`normalize_metadata_filters()` 校验；未知字段直接失败。

### 确定性解析

`DeterministicQueryUnderstanding` 负责：

- 识别测试用例、缺陷、需求、文档意图；
- 识别常见模块别名；
- 提取 `P0`–`P3`、版本号、业务编号；
- 生成可解释关键词；
- 不调用 LLM、不访问数据库。

它只覆盖当前测试语料的常见写法，不声称理解同义词、复杂否定或多跳意图。

### LLM Structured Output

`LLMQueryUnderstanding` 负责：

- 要求模型只返回 JSON；
- 支持 Markdown JSON 围栏；
- 用 Pydantic 严格校验字段、枚举和过滤条件；
- 拒绝未知字段；
- 失败后重试，重试耗尽后可回退确定性解析；
- 用 `accepted_by` 记录接受路径。

LLM 输出不是最终证据，也不能直接决定 Gold 文档或引用来源。

## 3. Query Rewrite 契约

核心文件：`app/query/rewrite.py`。

| 策略 | 行为 | 默认 |
|---|---|---|
| `noop` | 原样返回，作为基线 | 是 |
| `deterministic` | 清理空白、应用显式别名 | 可选 |
| `llm` | 生成单行检索文本，失败时降级 | 否 |
| `composite` | 按顺序尝试多个策略 | 可选 |

`RewriteResult` 记录：

```text
original_query
search_query
strategy
changed
fallback_reason
```

LLM 输出必须是单行、非空文本；多行、空输出或调用失败时回退，并在
`fallback_reason` 中记录原因。

## 4. 候选评测结果

### 4.1 Phase 16：Query Rewrite

当前 manifest 使用 Phase 8 的 24 条模拟 Query、BM25 检索与 `candidate` 模式：

| 策略 | Recall@5 | MRR | 改写 Query 数 | Rewrite P95 |
|---|---:|---:|---:|---:|
| `noop` | 0.944 | 0.792 | 0 | 约 0.000002s |
| `deterministic` | 0.944 | 0.792 | 0 | 约 0.000003s |

这组数据说明：

- 当前模拟 Query 没有触发确定性改写，因此没有收益也没有损失；
- 不能据此宣称 Query Rewrite 无效；
- 也不能据此宣称 Query Rewrite 有收益。

Phase 8 的 24 条模拟 Query 上，Query Understanding 只做工具链覆盖检查：

- 24 条都生成结构化结果；
- 4 条识别出具体 intent；
- 19 条生成了可投影的 Metadata Filter。

这些数字只验证 Schema、规则和运行器，不是业务准确率。

### 4.2 Phase 15：Phase 12 候选集

独立的 Phase 12 投影评测使用 `evaluate_phase12_understanding()`，不会把
Phase 12 的 100 条 Query 送进 Phase 6 的 `ChunkingDataset` loader。当前结果
来自 `results/query-understanding-phase12.json`：

| 指标 | 结果 | 口径 |
|---|---:|---|
| 候选 Query | 100 | 全部 `pending` |
| 生成结构化结果 | 100 | Pydantic Schema 通过 |
| 识别具体 intent | 29 | `intent != general_search` |
| 生成非空 filters | 58 | `to_metadata_filters()` 非空 |
| 覆盖预期过滤条件 | 12/20 | expected filters 被解析结果覆盖 |

这里的“覆盖预期过滤条件”允许解析结果包含额外字段（例如从“登录”推断出
`module=login`），但要求每个预期字段和值都出现；`tags` 等数组要求包含全部
预期值。它不是意图准确率，也不是检索效果，因为 Phase 12 的
`expected_intent` 是业务描述文本，不是 Phase 15 的受控枚举。

Phase 12 的 100 条候选全部仍为 `pending`，当前报告不能用于宣称 Query
Understanding 已达到正式质量门槛。要评估真实收益，必须先完成人工确认，
再用同一份 confirmed Gold Set 比较 noop / deterministic / LLM rewrite。

## 5. 复现

```bash
uv run python scripts/query_understanding_experiment.py --dry-run
uv run python scripts/query_understanding_experiment.py

uv run python scripts/query_rewrite_experiment.py --dry-run
uv run python scripts/query_rewrite_experiment.py
```

当前没有接入真实 LLM 的离线批量评测；接入前必须固定模型、Prompt、重试策略
与延迟预算，并使用人工确认的 Gold Set 才能判断是否保留 Rewrite。
Phase 16 CLI 当前显式拒绝 `--mode formal`：它只接受没有审阅状态的 Phase 8
数据，不能把“命令行写了 formal”当成正式 Gold Set。

## 6. 当前限制与后续

1. `candidate` 数据不能作为正式 Recall / MRR 结论。
2. 当前确定性规则只覆盖小语料中的常见模块、优先级、版本和业务编号。
3. 中文 `semantic_query` 保留原问题；是否应改为真正的语义改写，需要
   Embedding 检索实验验证。
4. LLM Structured Output 已实现校验和重试，但尚未用真实模型做批量评测。
5. 是否将 Query Understanding / Rewrite 接入在线 RAG，必须等正式 Gold Set
   和延迟预算确认后再决定。
6. Phase 12 的 `expected_intent` 与 Phase 15 的受控 intent 尚未建立人工
   映射，因此当前报告刻意不输出“意图准确率”。
7. Phase 16 尚未接入人工确认 Phase 12 数据的 formal 入口；当前 formal
   模式会被 CLI 拒绝，而不是伪造 `formal_gold_set=true`。
