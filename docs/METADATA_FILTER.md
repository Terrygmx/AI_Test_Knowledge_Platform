# Phase 10：Metadata Filter 实验

- 日期：2026-10-20
- 分支：`phase-10-metadata-filter`
- 数据集：`eval/fixtures/phase10_simulated.json`
- 数据集 SHA-256：`34bccafb4551e47d3998756cf8c007023e1b28d9f0d157c452d95485f5e00726`
- 固定 Chunk：`entity+heading`，22 条 Query / 47 个 Chunk
- Embedding：本机 OMLX `bge-m3-mlx-4bit`（1024 维）
- 数据库：隔离库 `aitkp_filter`（PostgreSQL 17 + pgvector 0.8.7）
- 原始 manifest：`results/metadata-filter-phase10.json`

## 1. 实验目标

Phase 10 固定 Phase 8 的 Chunk 与检索实现，只增加 Metadata Filter，回答：

1. 过滤条件是否真正发生在候选截断之前？
2. “先过滤再检索”与“先取 Top-K 再过滤”在 Recall 上差在哪里？
3. 过滤缩小候选集后，是否仍能召回人工标注的 Gold 证据？
4. SQLite 与 PostgreSQL 是否对同一组条件给出一致语义？

本实验不评价 Rerank、生成答案质量、生产 API 延迟或 ANN 参数。

## 2. 过滤 DSL

支持字段：

| 字段 | 类型 | 语义 |
|---|---|---|
| `module` / `sub_module` | 字符串 | 精确匹配 |
| `priority` / `version` | 字符串 | 精确匹配 |
| `status` / `severity` | 字符串 | 精确匹配 |
| `case_type` / `doc_type` | 字符串 | 精确匹配 |
| `tags` / `related_cases` | 字符串数组 | 包含任一值（OR） |

不同字段之间是 **AND**。例如：

```json
{
  "module": "payment",
  "priority": "P0",
  "tags": ["smoke", "security"]
}
```

表示 `module=payment AND priority=P0 AND (tags 包含 smoke OR security)`。

约定：

- 缺失字段、`null`、类型错误都不匹配，不能把“没有元数据”当成满足条件；
- 未知字段、空数组、空白值、非字符串元素直接报错，不能静默忽略；
- 标量值去首尾空白但保留大小写；数组去重并保留原值；
- 空对象 `{}` 等价于不过滤；`[]`、`""`、`0` 等非对象输入直接报错。

实现位于 `app/retrieval/filters.py`。`KnowledgeChunkService.search()`、
`lexical_search()` 与 `hybrid_search()` 接受同一份 `metadata_filters`；
Hybrid 的两个通道使用完全相同的条件。

## 3. 四种变体

实验比较：

| 变体 | 执行方式 | 用途 |
|---|---|---|
| `none` | 不过滤，直接排序取 Top-K | 基线 |
| `pre` | 先用 SQL `WHERE` 缩小候选，再排序取 Top-K | 生产推荐路径 |
| `post` | 先取未过滤 Top-K，再过滤且不补候选 | 复现候选饥饿 |
| `during` | 当前复用 `pre` 的 SQL 过滤路径 | 预留 ANN 中过滤实验位 |

`post` 是故意保留的失败路径：如果目标 Chunk 排在未过滤结果第 N 位，而前 N-1
位都被 Metadata 条件过滤掉，最终可能返回空结果或少于 K 条。不能通过“后过滤后
再补候选”来掩盖这个路径，否则测到的就不是调用方实际会遇到的召回损失。

**重要边界**：本轮实验表没有 HNSW。`during` 不是独立的 pgvector ANN 过滤实验，
只是与 `pre` 相同的 SQL 预过滤执行别名；manifest 的
`retrieval.variant_execution.during` 明确记录这一点。要研究 HNSW 下过滤对 Recall
的影响，需要另建带索引的实验表，并记录 pgvector 的 filtering / iterative scan
行为，不能把本轮结果外推为 ANN 结论。

## 4. SQLite 与 PostgreSQL 实现差异

两边都在 SQL `WHERE` 中过滤，且在 `LIMIT` 之前应用。

- PostgreSQL：`metadata` 是 JSONB；数组字段对每个期望值构造单元素 JSONB 数组，
  分别执行 `@>` 后用 OR 组合。不能把多个值放进一个 `@>`，否则语义会变成
  “包含全部”而不是“包含任一”。
- SQLite：使用 `json_each` 展开数组，并要求 `json_type(...)= 'array'`；
  缺失字段、标量或 `null` 不会产生匹配。
  SQLite 通过 `json_quote(value) IN ('"expected"')` 锁定 JSON 字符串；数字
  `1` 会产生 `'1'`，不会与字符串 `"1"` 的 `'"1"'` 匹配。
  PostgreSQL 通过 `jsonb_typeof(...)= 'string'` / `'array'` 和 JSONB containment
  保持同样的类型边界；标量数字、布尔和混合类型数组不会静默命中字符串过滤。

同一组语义有跨方言测试守护，包含标量精确匹配、数组 contains-any、多字段 AND、
缺失 / 错误类型、过滤先于 `LIMIT` 以及 PG 的 JSONB 编译结果。

## 5. 结果

正式结果由干净提交生成，原始数字以 `results/metadata-filter-phase10.json` 为准。
运行配置：`candidate_k=20`、`top_k=5`、RRF `1:1, k=60`。

| 变体 | Recall@5 | MRR | 空结果 Query | Under-K Query | 不可回答 FPR | Gold 被过滤 Query |
|---|---:|---:|---:|---:|---:|---:|
| `none` | 0.972 | 0.794 | 0 | 0 | 1.000 | 0 |
| `pre` | 1.000 | 0.931 | 0 | 15 | 1.000 | 0 |
| `post` | 0.972 | 0.903 | 1 | 21 | 0.750 | 0 |
| `during` | 1.000 | 0.931 | 0 | 15 | 1.000 | 0 |

过滤后的候选数从 1034 降到 188（按 Query 累计候选数），说明过滤确实缩小了
检索空间；它没有误杀任何人工标注的 Gold 证据。`post` 出现 1 条空结果，直接
展示了候选饥饿的风险。

4 条不可回答 Query 在 `none` / `pre` / `during` 全部返回了 top-1 近邻
（FPR=1.000），`post` 的 FPR 是 0.750。这个数字只说明小样本下向量检索倾向
返回相似但不相关的候选，不能外推成整体误召回率。Metadata Filter 只约束
候选空间，不负责“无证据拒答”；后续 Rerank 或 RAG 评估需要单独跟踪拒答、
相关性和负例指标。

`candidate_k=20` 只控制 RRF 中向量与 BM25 两条通道的候选深度；`vector` /
`keyword` 单通道直接使用 `top_k`，`post` 先取未过滤 `top_k` 再过滤，不补取
候选。Under-K Query 表示最终结果少于 `top_k=5` 的 Query 数，用来与空结果
一起暴露候选饥饿的范围。

这些数字只覆盖 22 条 Query / 47 个 Chunk 的模拟数据。`none` 与 `post` 的
Recall 相同不代表后过滤安全：`post` 已经出现空结果，且候选饥饿在更大的数据上
可能表现为少返回或多漏召回。当前样本也不足以校准生产阈值或给出统计显著性。

## 6. 延迟边界

`query_p50_seconds` / `query_p95_seconds` 包含启用时的 SQL 预过滤、候选取回、
排序与融合，不包含 Query Embedding 和建表 / Chunk Embedding。因此跨变体延迟
比较的工作负载不同：`none` / `post` 不做 SQL 预过滤，`pre` / `during` 会执行
SQL 过滤并取回结果。实验表没有 HNSW，47 行使用精确扫描，因此这些数字不能作为
ANN、数据库 P95 或 API P95 结论。

SQL 预过滤本身会把过滤后的候选从数据库读回实验进程，再在内存中做向量 / BM25
排序。这是为了在固定 47 个候选上比较过滤变体，不是生产查询路径的性能基准。

## 7. 可复现性与正式运行

先提交代码与文档，再从干净工作区运行：

```bash
set -a; source .env; set +a
uv run python scripts/metadata_filter_experiment.py \
  --database-url "postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST:-127.0.0.1}:${POSTGRES_PORT:-5432}/aitkp_filter" \
  --output results/metadata-filter-phase10.json
```

运行后检查 manifest：

- `git_dirty` 必须为 `false`；
- `git_dirty` 覆盖关键路径的 tracked 与 untracked 变化；`review/`、`results/`
  和 `eval/fixtures/.gitkeep` 是允许的非关键产物例外；
- `dataset.sha256` 必须与本文一致；
- `chunks.count` 必须为 47；
- `retrieval.ann_index` 必须为 `null`；
- `retrieval.variant_execution.during` 必须说明它不是独立 ANN 实验。
- `retrieval.latency_scope` 必须与第 6 节口径一致；
- `retrieval.candidate_k_semantics` 必须说明该参数只作用于 RRF 通道深度；
- 每个 result 必须包含 `below_top_k_queries`。

离线演练不会调用模型或数据库，只验证过滤语义与候选饥饿：

```bash
uv run python scripts/metadata_filter_experiment.py --dry-run
uv run python scripts/metadata_filter_experiment.py --offline \
  --output /tmp/metadata-filter-phase10-offline.json
```

离线模式使用固定占位向量，只用于契约演练，不能当作语义质量结论。

## 8. 当前限制与下一步

1. Gold Set 只有 22 条 Query、26 条证据标注，且是模拟数据，不能外推到生产分布；
2. 尚未验证 HNSW / IVFFlat 下的过滤行为、iterative scan 与参数敏感性；
3. 尚未测量过滤选择性（selectivity）对延迟和召回的影响；
4. 统一 `/search` 与 RAG 仍未接入 Metadata Filter，公开契约保持不变；
5. 生产接入前需要决定过滤条件从哪里来（显式 API 参数、意图解析还是两者结合），
   不能从 Query 文本静默猜测；
6. 大库需要评估 JSONB GIN 索引、数组过滤的选择性与查询计划，而不是只看小样本
   的绝对延迟。
