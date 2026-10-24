# Phase 13：Retrieval Evaluation

- 日期：2026-10-22
- 分支：`phase-12-evaluation-dataset`
- 数据集：`eval/fixtures/phase8_simulated.json`
- 数据集 SHA-256：`be85ce63b760207368bf229a6484235a430360aee82d82ca81e45d42c0f186a1`
- 原始 manifest：`results/retrieval-phase13.json`
- 运行模式：`candidate`
- 状态：**指标与 Badcase 归因已完成；当前结果不是正式 Gold Set 结论**

## 1. 目标与边界

Phase 13 自己实现并解释以下检索指标：

```text
Recall@K
Precision@K
MRR
NDCG@K
```

本阶段只评价 Chunk 级检索，不评价 Rerank、生成答案、Faithfulness 或
Citation Accuracy。当前 manifest 使用 Phase 8 的 24 条模拟 Query（18 可回答 /
6 不可回答）和 47 个 `entity+heading` Chunk；Phase 12 的 100 条候选虽然可以
通过 `--phase12-dataset` 跑通流程，但全部仍是 `pending`，结果只能作为
**探索性**数据，不是正式 Gold Set。

## 2. 指标定义

### 2.1 Recall@K

```text
Recall@K = Top-K 命中的 Gold 证据数 / 该 Query 的 Gold 证据总数
```

Gold 使用集合语义，重复标注不会重复计数。多证据 Query 需要逐条证据命中，
不能因为同一来源的一条 Chunk 命中就认为其他 Gold 证据也被召回。

### 2.2 Precision@K

```text
Precision@K = Top-K 命中的 Gold 证据数 / K
```

当实际返回少于 K 条时，分母使用实际返回数量，避免把“没有返回”错误当成
“返回了不相关结果”。但报告同时保留 `result_count`，方便识别检索饥饿。

### 2.3 MRR

```text
MRR = mean(1 / 第一条相关结果的排名)
```

MRR 只关心第一条相关结果出现得多早，不区分后续相关结果数量。它适合观察
“是否把最相关证据排到前面”，不能替代 Recall。

### 2.4 NDCG@K

当前使用二值相关性：

```text
DCG@K  = Σ(rank=1..K) gain(rank) / log2(rank + 1)
IDCG@K = 理想排序下的 DCG@K
NDCG@K = DCG@K / IDCG@K
```

每条 Gold 证据 gain=1。NDCG 对“相关结果出现在更靠前的位置”更敏感，因此
同一 Recall 下可以区分排序质量。当前没有使用分级相关性，不能把该结果解释为
带权重的业务收益。

## 3. Badcase 归因

`query_metric_details()` 输出逐 Query 的指标与检索层 Badcase 初筛：

| 类型 | 含义 |
|---|---|
| `empty_results` | Top-K 没有返回任何结果 |
| `wrong_source` | 返回了 Gold 来源，但正文不包含 Gold 证据片段 |
| `no_gold_hit` | Top-K 没有命中任何 Gold 证据 |
| `ranked_below_k` | 证据存在，但第一条相关结果排在请求的 K 之外 |
| `unexpected_results` | 不可回答 Query 却返回了结果 |

这些标签只描述**检索层现象**，不能直接归因于 Embedding、Chunking、Rerank 或
生成模型。Phase 14 需要另外区分 Retrieval Error 与 Generation Error。

## 4. 总体结果

下表来自 `results/retrieval-phase13.json`，四舍五入到 3 位小数：

| 检索器 | Recall@5 | Precision@5 | MRR | NDCG@5 | Badcase 数 |
|---|---:|---:|---:|---:|---:|
| `ilike` | 0.056 | 0.056 | 0.056 | 0.056 | 17 |
| `bm25` | 0.944 | 0.400 | 0.792 | 0.627 | 6 |

BM25 的 Recall@5 为 0.944、MRR 为 0.792；ILIKE 几乎只能命中直接包含完整
Query 文本的样本。该差异说明“整串子串匹配”不能处理组合标识符、版本号与
语义改写，但不代表 BM25 在生产查询分布上同样有效。

## 5. 分类结果

BM25 的分类结果如下：

| 分类 | Recall@5 | Precision@5 | MRR | NDCG@5 |
|---|---:|---:|---:|---:|
| `exact_identifier` | 1.000 | 0.733 | 0.833 | 0.667 |
| `exact_priority` | 0.833 | 0.267 | 0.667 | 0.409 |
| `exact_version` | 1.000 | 0.533 | 0.667 | 0.538 |
| `semantic_paraphrase` | 0.944 | 0.289 | 0.861 | 0.717 |
| `unanswerable` | 0.000 | 0.000 | 0.000 | 0.000 |

`unanswerable` 的 Recall/Precision 没有意义；该分类应看误召回率。BM25 的
6 条不可回答 Query 都返回了 Top-10 结果，因此 `badcase_count=6`。非空结果
不能当成“有足够证据”，拒答阈值仍需单独校准。

## 6. 结果解释

1. **Recall 与 Precision 不同**：BM25 的 Recall@5 很高，但 Precision@5 只有
   0.400，说明它能把 Gold 证据找回来，也会同时返回较多无关 Chunk。
2. **MRR 与 NDCG 补充排序信息**：BM25 的 MRR 0.792、NDCG@5 0.627，说明
   第一条相关结果通常较靠前，但多证据 Query 的理想排序仍未达到。
3. **负例全部误召回**：融合或词法检索只改变排序，不自动具备拒答能力。
4. **小样本结论有限**：24 条 Query 只用于验证指标实现与失败模式，不能据此
   宣称 BM25 在生产环境优于其他检索器。

## 7. 复现

```bash
# 离线评测，不调用模型或数据库
uv run python scripts/retrieval_evaluation.py \
  --output results/retrieval-phase13.json

# Phase 12 候选集流程演练（仍是 pending，只能 candidate）
uv run python scripts/retrieval_evaluation.py \
  --phase12-dataset eval/fixtures/phase12_candidates.json \
  --corpus eval/fixtures/phase6_simulated.json \
  --mode candidate \
  --output results/retrieval-phase13-candidates.json

# 人工确认后正式运行；当前 pending 数据会被拒绝
uv run python scripts/retrieval_evaluation.py \
  --phase12-dataset eval/fixtures/phase12_confirmed.json \
  --corpus eval/fixtures/phase6_simulated.json \
  --mode formal \
  --output results/retrieval-phase13-formal.json
```

manifest 记录数据集与语料哈希、运行模式、指标定义、逐 Query 细节和
`result_sha256`。延迟字段只作环境参考，不参与结果哈希。

## 8. 已知局限

- 当前没有人工确认的 100 条正式 Gold Set；
- 只有 24 条模拟 Query，分类样本很小；
- 二值 NDCG 没有业务分级权重；
- Badcase 只做检索层初筛，尚不能区分 Chunking / Embedding / Rerank 根因；
- 不可回答 Query 尚未接入统一 `max_distance` 或拒答策略；
- 当前 ILIKE / BM25 在内存中运行，延迟不代表生产 API P95。

## 9. 下一步

1. 完成 Phase 12 的人工逐条确认，生成正式 Gold Set；
2. 在正式数据上重跑 ILIKE / BM25 / Vector / Hybrid，并固定同一份 manifest；
3. 为 `ranked_below_k` 与 `wrong_source` 增加按通道、分类和候选深度的归因；
4. 进入 Phase 14 后，把 Retrieval Error 与 Generation Error 分开评测。
