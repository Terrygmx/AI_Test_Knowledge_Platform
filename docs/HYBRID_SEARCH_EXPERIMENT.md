# Phase 9：Hybrid Search 实验

- 日期：2026-10-20
- 分支：`phase-9-hybrid`
- Embedding：本机 OMLX `bge-m3-mlx-4bit`（1024 维）
- 数据库：隔离库 `aitkp_hybrid`（PostgreSQL 17 + pgvector 0.8.7）
- 数据集：`eval/fixtures/phase8_simulated.json`
- 数据集 SHA-256：`da92624f5f287fcc83943fb68ebc6d969d6d71b35e5b519215c687606e996150`
- 固定 Chunk：Phase 8 `entity+heading`，24 条 Query / 47 个 Chunk
- 原始 manifest：`results/hybrid-phase9.json`
- 参数敏感性 manifest：`results/hybrid-sweep-phase9.json`

## 1. 实验目标

Phase 9 固定数据、Chunk 粒度、Embedding 和 BM25 实现，只改变候选融合方式，
回答三个问题：

1. Vector 与 BM25 在同一批 Chunk 上各自表现如何？
2. 每个通道内部归一化后的 Weighted Score，能否互补两种失败模式？
3. 只依赖名次的 RRF，是否比需要调权重的 Weighted 更稳健？

本实验只评价 Chunk 级检索，不评价 Rerank、生成答案质量或生产 API 的端到端延迟。

## 2. 数据与身份口径

实验复用 Phase 8 的 24 条 Query（18 可回答 / 6 不可回答）和 47 个 Chunk：
12 个 TestCase、6 个 Defect、5 个 Requirement、24 个 Document Section。
可回答 Query 共 26 条证据标注。

两个通道必须使用完全相同的 Chunk，而且候选身份是：

```text
(source_type, source_id, chunk_index)
```

不能只按 `source_type + source_id` 去重。同一文档或实体可能有多个 Chunk，只按
来源合并会把不同证据片段错误压成一条。`RetrievedChunk` 与实验表均保存真实
`chunk_index`，并有唯一性测试守护这一点。

Gold Set 仍用“来源正确 + 正文包含证据片段”判断命中，不绑定 Chunk ID。

### 2.1 为什么使用 Phase 8 数据

Phase 8 专门加入了组合关键词坏例：

```text
P0 BUG-1234 login-api v1.3
```

其中 `BUG-1234` 在 Phase 8 数据集里是新增 Defect。它适合观察 BM25 对离散标识符
的优势，也能检验融合是否会把词法命中与语义候选正确合并。代价是 Phase 9 与
Phase 8 共用同一份小样本 Gold Set，不能把调参后的最高值当成独立验证。

## 3. 两种融合方法

### 3.1 Weighted Score

向量通道返回 cosine distance（越小越好），BM25 返回 relevance score
（越大越好），两者量纲不可直接相加。实现先在**每个通道内部**做 min-max：

```text
vector_similarity = (max_distance - distance) / (max_distance - min_distance)
keyword_normalized = (bm25 - min_bm25) / (max_bm25 - min_bm25)
final = vector_weight * vector_similarity + keyword_weight * keyword_normalized
```

如果通道内所有分数相同，统一归一化为 1.0。只出现在一个通道的候选，另一通道
贡献记为 0。实际实现直接对 distance 取负再归一化，与上式等价。

这种方法的优点是保留分数差距；缺点是归一化范围来自当次候选集合，候选深度变化
可能改变最终排序。

### 3.2 Reciprocal Rank Fusion

RRF 只使用排名：

```text
score(candidate) = Σ channel_weight / (rrf_k + rank)
```

`rank` 从 1 开始，默认 `rrf_k=60`。它不依赖分数量纲，也不需要把 distance
转换为 similarity；代价是丢弃了同一通道内部的分数差距。

### 3.3 实现边界

- 融合器假设每个通道已按自己的方向排好序，不再用统一降序重排；
- 候选按 `(source_type, source_id, chunk_index)` 去重；
- 支持一个通道为空，并保留 BM25 的 `matched_terms` 与正文；
- 最终排序按融合分数降序，再以来源类型、来源编号、Chunk 序号稳定打破平局。

真实实验的向量候选走 pgvector `<=>`；纯 Python cosine 仅用于离线单元测试。

## 4. 实验配置

| 配置 | 值 |
|---|---|
| 候选深度 `candidate_k` | 20 |
| 最终 `top_k` | 10 |
| Weighted 默认权重 | vector:keyword = 1:1 |
| RRF 默认参数 | 1:1，`k=60` |
| BM25 | `k1=1.2`、`b=0.75` |
| Embedding batch / timeout / retries | 32 / 60s / 2 |
| 向量索引 | 无 HNSW，47 行精确扫描 |

比较四个通道：

```text
Vector
Keyword (BM25)
Weighted (1:1)
RRF (1:1, k=60)
```

## 5. 结果

### 5.1 总体检索质量

| 通道 | Recall@5 | Recall@10 | MRR | Query P50 | Query P95 |
|---|---:|---:|---:|---:|---:|
| Vector | 0.917 | 0.972 | 0.748 | 0.0021s | 0.0033s |
| Keyword | 0.944 | 0.972 | 0.792 | 0.0001s | 0.0002s |
| Weighted 1:1 | 0.972 | 0.972 | 0.764 | 0.0019s | 0.0029s |
| RRF 1:1, k=60 | 0.972 | 0.972 | 0.794 | 0.0018s | 0.0046s |

26 条证据标注中，Vector 在 Top-5 命中 23 条，Keyword 命中 24 条，
Weighted / RRF 均命中 25 条。Top-10 下四个通道都命中 25 条。

RRF 的 MRR 只比 BM25 高约 0.002，Recall@5 只多命中 1 条证据。这个差异在当前
24 条 Query 上不足以证明 RRF 在生产分布上稳定优于 BM25。

### 5.2 不可回答 Query

| 通道 | 误召回率 | top1 distance 最小值 | top1 distance 中位数 |
|---|---:|---:|---:|
| Vector | 1.000 | 0.280 | 0.384 |
| Keyword | 1.000 | 0.000 | 0.000 |
| Weighted 1:1 | 1.000 | 0.000 | 0.000 |
| RRF 1:1, k=60 | 1.000 | 0.000 | 0.000 |

6 条不可回答 Query 在所有通道的 Top-10 中都有结果。融合只改变排序，不具备
“没有足够证据就拒答”的能力。Keyword / Weighted / RRF 的 distance 列没有意义：
这些通道不是纯向量距离，manifest 中对应值为占位 0，不能据此设置
`max_distance`。

### 5.3 延迟口径

表中的 Query P50 / P95 只包含：

```text
pgvector 候选查询 + BM25 查询 + 融合 / 排序
```

不包含 Query Embedding、数据库连接建立、结果转 ORM 或 RRF 之后的 Rerank。
整个 24 条 Query 的 Embedding 耗时约 0.551s，47 个 Chunk 的 Embedding + 写库
约 1.603s。这些数字来自同一台本机，批次与负载变化会影响绝对值。

实验表只有 47 行且未建 HNSW，pgvector 使用精确扫描。因此这里不能得出 ANN 查询
性能结论，也不能把毫秒级差异解释为某个融合策略更快。

## 6. 参数敏感性

下表来自 `results/hybrid-sweep-phase9.json`。`Recall@10` 在除 2:1 RRF 外的组合中
都为 0.972，表中主要保留 Recall@5 与 MRR。

| 方法 | vector:keyword / k | Recall@5 | MRR |
|---|---|---:|---:|
| Weighted | 1:1 | 0.972 | 0.764 |
| Weighted | 2:1 | 0.972 | 0.752 |
| Weighted | 1:2 | 0.972 | 0.773 |
| Weighted | 3:1 | 0.944 | 0.736 |
| Weighted | 1:3 | 0.972 | 0.773 |
| RRF | 1:1, k=60 | 0.972 | 0.794 |
| RRF | 2:1, k=60 | 0.944 | 0.796 |
| RRF | 1:2, k=60 | 0.972 | 0.810 |
| RRF | 3:1, k=60 | 0.944 | 0.796 |
| RRF | 1:3, k=60 | 0.972 | 0.782 |

RRF 的 `k` 在 1:1 下扫描 10 / 20 / 40 / 60 / 100：

| k | Recall@5 | MRR |
|---:|---:|---:|
| 10 | 0.972 | 0.796 |
| 20 | 0.972 | 0.794 |
| 40 | 0.972 | 0.794 |
| 60 | 0.972 | 0.794 |
| 100 | 0.972 | 0.794 |

`k=10` 的 MRR 略高、`1:2` RRF 的 MRR 最高，但这些都是在同一份 24 条 Query
上扫描参数得到的结果，存在明显的调参过拟合风险。默认仍建议保持 RRF `1:1`、
`k=60`，不根据一次小样本扫描改变生产默认值。

## 7. 结果解释

1. **BM25 在本数据集上强于 Vector**：业务编号、版本、优先级和模块名是离散符号，
   词法通道保留得更完整；在 26 条证据中，BM25 Top-5 比 Vector 多命中 1 条。
2. **融合确实补上了一个召回缺口**：Weighted / RRF 的 Recall@5 都达到 0.972，
   比两个单通道更完整；但提升只对应 1 条证据，不能夸大。
3. **Weighted 的 MRR 低于 BM25**：min-max 后两个通道的分数仍可能让非目标证据
   排到前面；RRF 只依赖名次，在当前数据上更稳。
4. **RRF 与 BM25 的差距很小**：MRR 0.794 vs 0.792，不足以证明混合检索已经
   带来实质性的排序收益；需要更大 Gold Set 和分类 Badcase 归因。
5. **不可回答 Query 没有改善**：6 条负样本全部误召回，说明融合不是拒答策略；
   阈值、负样本校准或 Rerank 不能混为“融合的收益”。

## 8. 能力接入边界

Phase 9 新增 `KnowledgeChunkService.hybrid_search()`，返回 `FusedHit`：

- 保留 `vector_rank` / `keyword_rank` / 原始分数；
- 保留 `matched_terms`，便于追踪 BM25 命中词项；
- 默认读取 `retrieval_vector_top_k` / `retrieval_keyword_top_k`、
  `retrieval_fusion`、`retrieval_rrf_k` 与 `retrieval_final_top_k`；
- 只在 PostgreSQL + pgvector 上运行，SQLite 明确报错，不伪造向量排序。

当前 **没有** 用 Hybrid 替换统一 `/search`，也 **没有** 改造 RAG。原因是：

- `/search` 的公开响应尚无 score 契约，直接在 HTTP 暴露融合分数会扩大 API 变更；
- BM25 每次查询都会全量读取 Chunk 并建内存索引，候选预过滤尚未完成；
- Metadata Filter 属于 Phase 10，Rerank 属于 Phase 11，提前把 Hybrid 接进 RAG
  会让后续变量无法隔离。

## 9. 复现

前置条件：

```bash
# 隔离库；不要把开发库或生产库作为目标
docker compose exec postgres psql -U postgres -d postgres \
  -c 'CREATE DATABASE aitkp_hybrid'
docker compose exec postgres psql -U postgres -d aitkp_hybrid \
  -c 'CREATE EXTENSION IF NOT EXISTS vector'
```

无副作用配置检查：

```bash
uv run python scripts/hybrid_experiment.py --dry-run
uv run python scripts/hybrid_sweep.py --help
```

真实实验：

```bash
uv run python scripts/hybrid_experiment.py \
  --database-url 'postgresql+psycopg://USER:PASSWORD@127.0.0.1:5432/aitkp_hybrid' \
  --output results/hybrid-phase9.json

uv run python scripts/hybrid_sweep.py \
  --database-url 'postgresql+psycopg://USER:PASSWORD@127.0.0.1:5432/aitkp_hybrid' \
  --output results/hybrid-sweep-phase9.json
```

脚本只允许写入库名包含 `hybrid` 的 PostgreSQL。每次运行会重建
`phase9_hybrid_chunks`，Embedding 与 Query 使用同一模型。manifest 记录数据集 /
候选 Chunk 哈希、`git_commit` / `git_dirty`、脱敏命令、模型 / 维度、Embedding
batch / timeout / retries、`/models` 元数据与完整指标。已提交的 manifest 必须来自
`git_dirty=false` 的同一提交；脏工作区结果只能作为临时实验。

## 10. 已知局限

- 只有 24 条 Query、47 个 Chunk，且与 Phase 8 共用 Gold Set；参数扫描结论存在
  同源选择与过拟合风险。
- 只评测 Recall@5 / Recall@10 / MRR，没有 Precision@K、NDCG、分类 Badcase 或
  统计显著性检验。
- 6 条负样本全部误召回；不能从当前结果确定拒答阈值。
- Weighted 的 min-max 范围随候选集合变化，跨查询的可比性和稳定性尚未验证。
- BM25 每次查询全量读取并建内存索引；服务层 Hybrid 尚未做候选预过滤。
- 实验表未建 HNSW，延迟只反映 47 行精确扫描；未测并发、冷启动和大库内存。
- Query 延迟不含 Query Embedding 总成本，不能当作 API P95。
- 尚未评测 Metadata Filter、Rerank、Query Rewrite 或生成 Faithfulness。

## 11. 结论与下一步

Phase 9 的有限结论是：

- Vector 与 BM25 都能作为独立检索通道；在当前测试知识语料上，BM25 的单通道
  MRR 更高；
- Weighted 与 RRF 都能把 Recall@5 提升到 0.972，但只比 BM25 多命中 1 条证据；
- RRF 的默认参数更少、对分数量纲更稳健，建议作为下一阶段默认融合接缝；
- 融合不能解决不可回答 Query 的误召回，必须与 Metadata Filter、阈值或 Rerank
  分开评价；
- 当前结果只能证明接口与失败模式，不能证明生产检索质量。

下一步进入 Phase 10：先用 Metadata Filter 缩小候选集，再验证过滤发生在向量
检索前 / 中 / 后对 Recall 和延迟的影响；不要在没有过滤和更大 Gold Set 的情况下
直接把 Hybrid 分数暴露到公开 API。
