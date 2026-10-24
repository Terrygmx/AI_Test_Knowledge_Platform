# Phase 14：Generation Evaluation

- 日期：2026-10-22
- 分支：`phase-12-evaluation-dataset`
- 数据集：`eval/fixtures/phase12_candidates.json`
- 评测样本 SHA-256：`6a689eed73b01549950ef2190029d234827e29d341aaf87749437e109fffa5c6`
- 响应夹具：`eval/fixtures/phase14_simulated_responses.json`
- 原始 manifest：`results/generation-phase14.json`
- 运行模式：`candidate`
- 状态：**工具链与 Badcase 归因已完成；当前结果不是真实 LLM 质量结论**

## 1. 目标与边界

Phase 14 需要回答两个不同的问题：

1. **Retrieval 是否提供了回答所需的证据？**
2. **在证据存在时，Answer 是否忠实、相关、可追溯，并在无证据时拒答？**

本阶段实现了一个可解释的确定性评测器，并把 Ragas 保留为可选适配层。
Ragas 未加入默认依赖，也没有在未固定模型、Prompt 与参数时伪造正式分数。

当前 manifest 使用 10 条 Phase 12 `pending` Query 与人工构造的模拟响应：

- 8 条可回答、2 条不可回答；
- 10 条均为 `pending`，`formal_gold_set=false`；
- `response_source=simulated`，只能验证指标实现、引用边界与 Badcase 归因；
- 不能把下面的数字解释为真实 LLM、Ragas 或人工 Gold Set 的质量结论。

## 2. 指标定义

### 2.1 Context Recall

当上下文与 Gold 都带 Chunk 身份时：

```text
Context Recall = 上下文中命中的 Gold Chunk 身份数 / Gold Chunk 身份总数
```

这是来源级、可重复的检索覆盖检查。若只有文本而没有来源元数据，则退化为
预期答案要点的原文覆盖检查；两种口径不能混为 Ragas 的语义 Context Recall。

### 2.2 Context Precision

```text
Context Precision = 上下文中匹配 Gold Chunk 身份的块数 / 上下文块总数
```

如果未提供 `context_sources` 或 Gold 来源，指标返回 `null`，而不是用
“上下文非空”冒充 1.0。`metric_case_counts` 记录每个指标实际可计算的样本数。

### 2.3 Faithfulness

按句拆分 Answer，去掉引用编号后，检查每个事实句是否能由上下文原文或字符片段
支撑：

```text
Faithfulness = 受支持的事实句数 / Answer 事实句总数
```

系统拒答句与明确的“证据不足”句视为受支持，不因没有事实证据而扣分。
这是确定性的字面 / 字符重合检查，不理解同义改写、否定、因果或实体消歧，
不能冒充 Ragas 的 LLM-as-a-judge Faithfulness。

### 2.4 Answer Relevancy

```text
Answer Relevancy = 0.5 × 问题词项覆盖率 + 0.5 × 预期答案要点覆盖率
```

这是轻量启发式指标，用来发现“答非所问”和“遗漏要点”，不是 Ragas
Answer Relevancy 的等价物。不可回答样本没有答案要点时，只使用问题词项部分。

### 2.5 Citation Accuracy

解析 Answer 中的 `[1]`、`[1,2]` 等引用编号：

```text
Citation Accuracy = 合法引用编号次数 / 全部引用编号次数
```

编号必须落在本次上下文范围内，并能对应到响应中真实返回的 `Citation.rank`；
若提供 `context_sources`，Citation 的来源还必须与对应 rank 的上下文块一致。
重复引用逐次计分；越界、错来源或事实回答完全无引用均计错。

### 2.6 拒答一致性

- 不可回答 Query 明确拒答：正确；
- 可回答 Query 被误拒答：`generation_error_refusal`；
- 不可回答 Query 被回答：`generation_error_false_answer`。

## 3. Badcase 归因

| Badcase | 含义 | 边界 |
|---|---|---|
| `retrieval_error` | 上下文没有召回 Gold 证据，或 Context Recall / Precision 为 0 | 先归因检索，不把缺少证据误判为模型幻觉 |
| `generation_error_refusal` | 可回答 Query 被误拒答 | 证据已存在但模型没有使用 |
| `generation_error_false_answer` | 不可回答 Query 被回答 | 模型越过了证据边界 |
| `generation_error_missing_answer_point` | 检索正确，但 Answer 遗漏预期要点 | 最典型的“Retrieval 正确但 Answer 错” |
| `generation_error_citation` | 引用编号越界、无对应 Citation，或事实回答完全没有引用 | 引用可追溯性错误 |
| `generation_error_unsupported_claim` | Answer 含有上下文无法支撑的事实句 | 与 Retrieval Error 分开 |

归因顺序是刻意的：先判断检索是否提供证据，再判断生成是否拒答、漏点、引用错误
或产生无依据事实。一个样本只输出一个主要 Badcase，原始指标仍完整保留。
因此“证据本身缺失且模型拒答”归为 `retrieval_error`，不会把正确拒答误报成
Generation Error；若证据已存在但模型仍拒答，才归为 `generation_error_refusal`。

## 4. 候选回归结果

下表来自 `results/generation-phase14.json`，四舍五入到 3 位小数：

| 指标 | 数值 |
|---|---:|
| 样本数 | `10` |
| Badcase 数 | `5` |
| Context Recall | 0.875 |
| Context Precision | 0.875 |
| Faithfulness | 0.750 |
| Answer Relevancy | 0.300 |
| Citation Accuracy | 0.700 |
| Refusal Correct | 0.900 |

这些数字只说明夹具中的错误类型被正确识别，不代表真实模型在 100 条正式
Gold Set 上的表现。`metric_case_counts` 显示 Context Recall / Precision 与
Answer Coverage 各有 2 条不可计算样本，聚合时分母没有混用 0 值。
Citation Accuracy 为 0.700，是因为越界引用、无引用事实回答与无依据事实句都按引用
错误计分；Faithfulness 从 0.850 调整为 0.750，反映修复拒答词污染后的真实结果。

## 5. Badcase 明细

| Query | Badcase | 说明 |
|---|---|---|
| `P12-TC-003` | `generation_error_unsupported_claim` | 答案含“自动发送短信”，上下文没有支撑 |
| `P12-DF-001` | `generation_error_missing_answer_point` | 检索到缺陷，但答案没有回答根因 |
| `P12-DOC-002` | `generation_error_citation` | 答案引用 `[2]`，本次只有 rank 1 |
| `P12-DOC-006` | `retrieval_error` | 上下文来源不匹配 Gold，先归因检索 |
| `P12-UN-002` | `generation_error_false_answer` | 不可回答的比特币退款问题被直接回答 |

其中 `P12-TC-003` 同时存在无依据事实；当前主要 Badcase 选择
`generation_error_unsupported_claim`，`faithfulness=0.5` 保留在原始指标中。

## 6. 复现

```bash
uv run python scripts/generation_evaluation.py --dry-run
uv run python scripts/generation_evaluation.py
uv run python scripts/generation_evaluation.py --mode formal
```

第三条命令当前应因 10 条样本全部 `pending` 而失败，这是正式 Gold Set 门槛，
不是缺陷。人工确认必须填写 `review_status=confirmed`、`reviewed_by` 与带时区的
`reviewed_at`，不得用脚本批量伪造。

## 7. 当前限制与后续

1. 当前没有真实 LLM 批量回答；模拟响应只验证评测器，不验证模型能力。
2. 当前没有人工确认的 Phase 12 Gold Set，正式 Faithfulness / Answer Relevancy
   结论仍需人工审阅。
3. Deterministic 指标不能理解同义改写、否定、因果和多跳推理；Ragas 适配层
   尚未固定版本、模型与参数，默认不启用。
4. 下一步应在人工确认数据后接入真实 RAG 输出，并同时报告检索指标、生成指标、
   Citation Accuracy 与延迟，避免只看单一分数。
