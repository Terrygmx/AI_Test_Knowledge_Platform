# Phase 12：Evaluation Dataset

- 日期：2026-10-20
- 分支：`phase-12-evaluation-dataset`
- 候选数据：`eval/fixtures/phase12_candidates.json`
- 语料：`eval/fixtures/phase6_simulated.json`
- 状态：**候选已生成，人工 Gold Set 尚未确认**

## 1. 目标与边界

Phase 12 建立约 100 条可复用的评测 Query，覆盖：

| 分类 | 数量 |
|---|---:|
| TestCase Search | 30 |
| Defect Search | 20 |
| Document QA | 20 |
| Cross Version | 10 |
| Multi-condition | 10 |
| Unanswerable | 10 |

本阶段只建立数据契约、候选集和人工确认流程，不产生检索质量结论。
`eval/fixtures/phase12_candidates.json` 由 AI 生成，全部为 `pending`，
**不能用于正式 Recall@K / MRR 或宣称 Gold Set 已人工确认**。

## 2. 为什么不扩展现有 `ChunkingDataset`

Phase 6–11 的 `ChunkingDataset` 只有 `relevant` 证据标签，服务于 Chunking /
Embedding / BM25 / Hybrid 实验。Phase 12 还需要：

- 六类固定配额；
- `expected_intent` 与 `expected_answer_points`；
- `gold_documents` 与 `gold_chunks` 双层证据；
- 候选生成者、审阅人、审阅时间与审阅状态；
- 正式评测必须全部人工确认的门槛。

因此新增独立契约 `app/evaluation/phase12_dataset.py`，不改变历史 fixture
和实验口径。

## 3. Schema

每条 Query 保存：

```json
{
  "query_id": "P12-TC-001",
  "query": "登录成功后系统会怎样创建会话？",
  "category": "testcase_search",
  "expected_intent": "定位与问题最相关的测试用例及预期结果",
  "expected_filters": {},
  "gold_documents": [
    {
      "source_type": "testcase",
      "source_id": "SHOP-LOGIN-001",
      "evidence": "服务端创建新的会话"
    }
  ],
  "gold_chunks": [
    {
      "source_type": "testcase",
      "source_id": "SHOP-LOGIN-001",
      "chunk_index": 0,
      "evidence": "服务端创建新的会话"
    }
  ],
  "expected_answer_points": ["确认成功登录会创建新会话"],
  "answerable": true,
  "generated_by": "ai-candidate-v1",
  "review_status": "pending"
}
```

`gold_documents` 的名字沿用开发计划，但 `source_type` 支持 TestCase / Defect /
Requirement / Document；它表示来源级证据，不是只允许 Markdown 文档。

## 4. 校验规则

加载候选数据时执行：

1. 总数必须为 100，六类数量必须严格等于 30 / 20 / 20 / 10 / 10 / 10；
2. `query_id` 与 Query 文本不能重复；
3. 可回答问题必须有文档证据、Chunk 证据与答案要点；
4. 不可回答问题不得有 Gold、答案要点或过滤条件；
5. `gold_documents` 的来源必须存在，证据必须是来源正文中的原文片段；
6. `gold_chunks` 必须能定位到真实 Chunk，证据必须出现在该 Chunk 正文中；
7. `gold_chunks` 的来源必须与 `gold_documents` 对应；
8. `expected_filters` 必须合法，且不能过滤掉 Gold Chunk；
9. `pending` 不得带审阅人 / 审阅时间；`confirmed` / `rejected` 必须有
   审阅人、带时区的审阅时间。
10. 审阅人必须是 `Terry`；`confirmed` / `rejected` 必须写 `review_notes`，
    `pending` 不得带审阅备注。

加载模式：

- `candidate`：允许 `pending`，用于候选审阅与工具链验证；
- `formal`：要求全部 Query 为 `confirmed`，否则直接失败。

## 5. 人工确认流程

1. 运行 `uv run python scripts/phase12_candidates.py --dry-run` 检查候选分布；
2. 生成 / 刷新候选：`uv run python scripts/phase12_candidates.py`；
3. 人工逐条检查 Query 意图、证据片段、Gold 来源、答案要点与不可回答性；
4. 人工确认后填写 `review_status=confirmed`、`reviewed_by=Terry`、
   `reviewed_at`（带时区）与 `review_notes`；
5. 运行 `uv run python scripts/phase12_candidates.py --check --mode formal`；
6. 只有第 5 步通过后，Phase 13 才能把该文件作为正式 Gold Set 使用。

禁止用脚本自动批量把 `pending` 改成 `confirmed`。审阅人和时间必须对应真实的
人工确认行为，否则数据集只是“AI 自评”。

审阅粒度约定：

- 证据片段使用**关键字句**：能直接支持答案的最小完整子句，不要求整段；
- 答案要点遵循**一个要点一个事实**：一个事实写一条，不把多个事实合并；
- `rejected` 必须在 `review_notes` 写明拒绝原因，`confirmed` 至少写明
  “证据充分”或具体修正点。

## 6. 当前限制

- 候选问题与证据定位由确定性规则生成，不代表真实用户 Query 分布；
- 当前语料只有 12 个 TestCase、5 个 Defect、5 个 Requirement 和 3 篇文档，
  100 条 Query 中有较多同源问题，存在覆盖集中风险；
- 文档 Chunk 序号绑定当前 `entity+heading` 切分；策略变化后必须重新校验；
- 尚未做人工确认，因此 Phase 12 只能标记为“候选数据集完成”；
- 尚未接入数据库表 `evaluation_cases` / `evaluation_results`；Phase 12 的
  fixture 先作为可复现实验资产，持久化在后续需要时再迁移。
