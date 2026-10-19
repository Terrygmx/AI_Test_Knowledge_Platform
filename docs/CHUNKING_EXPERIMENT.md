# Phase 6：Chunking 对比实验

- 日期：2026-10-20
- 分支：`phase-6-chunking`
- Embedding：本机 OMLX `bge-m3-mlx-4bit`（1024 维）
- 数据库：隔离库 `aitkp_chunking`（PostgreSQL 17 + pgvector）
- 数据集：`eval/fixtures/phase6_simulated.json`
- 数据集 SHA-256：`117b9e154f29b1363ed6e7e14b224903180780b4c8b49e2ca6db47eb5ba62262`
- 原始 manifest：`results/chunking-2026-10-20.json`

## 1. 实验目标

在同一份语料和 Query 集上比较固定字符切分、语义切分与实体/章节级切分，观察
Chunk 粒度对 Recall@K、MRR、索引耗时和 Query 延迟的影响。

本实验只回答**检索层**问题，不评价生成质量。`Recall@K` 使用
`source_type + source_id + 原文证据片段` 匹配，不绑定 Chunk ID；切分方式和
Chunk 编号变化后仍可复用同一 Gold Set。

## 2. 数据与标注

### 2.1 模拟语料

共 40 条 Query，其中 34 条可回答、6 条不可回答；包含：

| 类型 | 数量 |
|---|---:|
| TestCase | 12 |
| Defect | 5 |
| Requirement | 5 |
| Markdown 文档 | 3 |

文档覆盖 checkout、支付失败排查和登录安全验收。业务内容均为本实验虚构，
未复制第三方正文。结构参考以下公开 QA 示例库：

- [ShopSphere QA manual testing portfolio](https://github.com/mohbasem25/qa-manual-testing)
- [DemoShop QA testing portfolio](https://github.com/PetrosChatz/qa-testing-portfolio)

### 2.2 标注口径

每条可回答 Query 标注一条或多条证据，粒度为：

```text
source_type + source_id + 原文证据片段
```

例如：

```text
document / SHOP-DOC-002 / "超时后使用原幂等键查询交易状态"
```

评测时，只要 Top-K 中某个 Chunk 的来源正确且包含该证据片段，即算命中。
这使 Gold Set 与切分策略解耦，避免“为某个 Chunk 编号调参”。

指标口径：

- **Recall@K**：每条 Query 命中的标注证据数 / 该 Query 的标注证据总数，再对 Query 取平均。
- **MRR**：第一条相关结果的倒数排名，再对可回答 Query 取平均。
- **不可回答误召回率**：不可回答 Query 在 Top-K 返回任意结果的比例。当前未设置
  `max_distance`，所以该值用于暴露问题，不代表生产阈值配置。
- **top1 distance**：不可回答 Query 第一条结果的 cosine distance，供后续阈值校准。

## 3. 策略配置

| 策略 | 配置 |
|---|---|
| `fixed-256` | 字符上限 256，overlap 64 |
| `fixed-512` | 字符上限 512，overlap 64 |
| `fixed-800` | 字符上限 800，overlap 64 |
| `fixed-1200` | 字符上限 1200，overlap 64 |
| `semantic` | 章节内相邻窗口 cosine 相似度 < 0.55 时切分；字符上限 800 |
| `entity` | TestCase / Defect / Requirement 一实体一 Chunk；文档一标题 Section 一 Chunk |

固定切分优先在段落、句子边界收口；单句超过上限时才硬切。overlap 是上一 Chunk
尾部的检索上下文，不改变 Chunk 主体 `start/end` 的原文范围。

Semantic Chunking 先以 Markdown 标题划分章节，再在章节内部比较相邻句窗的
cosine 相似度。标题作为章节锚点保留在正文中，不单独生成 Chunk；章节切换本身
是结构边界，章节内部低于阈值的相邻句窗才形成语义边界。这个设计避免了整篇统一
阈值时“标题被孤立”或“全文完全不去切”的两种失效模式。

## 4. 结果

### 4.1 检索质量与成本

| 策略 | Chunks | 平均字符 | 中位字符 | 最大字符 | Recall@5 | Recall@10 | MRR | 索引耗时 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| fixed-256 | 30 | 175.5 | 173.0 | 255 | 0.946 | 0.985 | 0.803 | 2.5s |
| fixed-512 | 27 | 195.1 | 165.0 | 492 | 0.936 | 0.990 | 0.789 | 5.1s |
| fixed-800 | 25 | 205.6 | 165.0 | 651 | 0.951 | 1.000 | 0.787 | 6.3s |
| fixed-1200 | 25 | 205.6 | 165.0 | 651 | 0.951 | 1.000 | 0.787 | 6.0s |
| semantic | 43 | 119.1 | 100.0 | 196 | **0.975** | 0.985 | **0.919** | 2.5s |
| entity | 46 | 115.6 | 101.5 | 196 | 0.966 | 0.985 | **0.919** | 2.7s |

Recall@5 命中数均为 59 条标注证据的子集：fixed-256 命中 56，fixed-512 命中 55，
fixed-800 / fixed-1200 命中 55，semantic 命中 57，entity 命中 56。

### 4.2 不可回答 Query

| 策略 | 误召回率 | top1 distance 最小值 | top1 distance 中位数 |
|---|---:|---:|---:|
| fixed-256 | 1.000 | 0.364 | 0.408 |
| fixed-512 | 1.000 | 0.359 | 0.408 |
| fixed-800 | 1.000 | 0.360 | 0.408 |
| fixed-1200 | 1.000 | 0.360 | 0.408 |
| semantic | 1.000 | 0.344 | 0.408 |
| entity | 1.000 | 0.334 | 0.408 |

6 条不可回答 Query 在 Top-10 中都能召回内容，说明“向量检索 Top-K 非空”不能
直接作为“有证据”的判断。当前数据量太小，不能据此选生产 `max_distance`；
下一步应在更大的 Gold Set 上比较阈值对拒答率和误拒率的影响。

### 4.3 Query 延迟

所有策略的 Query P95 都在 0.075–0.081 秒，差异主要来自检索结果数量与数据库
状态，而不是切分本身。该数字不包含 Embedding 请求耗时，仅适合在同一环境下
做相对参考。

## 5. 解释

1. **semantic 的 MRR 明显最好**：把章节级语义边界保留下来后，首个相关结果更靠前；
   相比 fixed-800 / fixed-1200 的 0.787，提升到 0.919。
2. **entity 与 semantic 接近**：两者都保留标题/实体边界，Chunk 更短且更聚焦；
   entity 的 Recall@5 略低，说明固定地“一实体一 Chunk”并不总是优于语义切分。
3. **fixed-800 与 fixed-1200 完全相同**：这 3 篇文档最长只有 651 字符，1200 上限
   没有任何作用；不能把这两个结果当作“大窗口更好”的证据。
4. **小语料上的高 Recall 不能外推**：只有 34 条可回答 Query、3 篇文档，
   每个策略的差异可能只对应少数样本。它是可复现的基线，不是生产结论。

## 6. 复现

```bash
# 只校验数据和策略，不调用模型、不写数据库
uv run python scripts/chunking_experiment.py --dry-run

# 真实实验；脚本只允许写入库名包含 chunking 的数据库
uv run python scripts/chunking_experiment.py \
  --database-url 'postgresql+psycopg://USER:PASSWORD@127.0.0.1:5432/aitkp_chunking' \
  --output results/chunking-2026-10-20.json
```

前置条件：

- PostgreSQL 已安装 `vector` 扩展；
- 目标库已通过 Alembic `upgrade head`；
- 本机 OMLX 可访问且 `bge-m3-mlx-4bit` 已加载。

脚本会清空并重建 `aitkp_chunking` 中的实验实体与 Chunk；不要把开发库或生产库
作为 `--database-url`。默认 `--dry-run` 不产生任何外部副作用。

## 7. 已知局限

- 语料与 Query 为模拟数据，规模远小于生产知识库，结论只用于方法验证。
- 只有中文为主的测试知识，未覆盖英文、代码块、表格和 PDF 版面噪声。
- `Recall@K` 以“包含证据片段”判断，可能高估“语义上能完整回答”的召回。
- 未评测 Precision@K、NDCG、Rerank 和生成答案的 Faithfulness。
- 固定切分按字符数衡量；不同 Embedding 模型的上下文窗口和 token 成本尚未纳入。
- Semantic 的阈值 0.55 基于当前模型和这批语料；换模型或语料后必须重新校准。

## 8. 结论与下一步

当前基线支持以下**有限**结论：

- 固定字符切分在短文档上会因窗口过大而退化，不能只比较窗口大小。
- 保留章节语义边界的 `semantic` 与 `entity` 在本实验中获得更高 MRR，值得在
  更大 Gold Set 上继续验证。
- 不可回答 Query 的 Top-K 误召回率仍然很高，检索结果非空不等于证据充分；
  阈值校准必须和拒答质量一起评测。

下一步应扩充到约 100 条人工确认 Query，并接入 Phase 7 的 Embedding 对比；
在进入 Hybrid Retrieval 前，先固定当前评测契约和数据隔离方式，避免切分、
Embedding 和融合策略同时变化导致无法归因。
