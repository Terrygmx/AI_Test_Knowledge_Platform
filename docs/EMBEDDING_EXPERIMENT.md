# Phase 7：Embedding 模型对比实验

- 日期：2026-10-20
- 分支：`phase-7-embedding`
- Embedding 服务：本机 OMLX，`http://127.0.0.1:28080/v1`
- 数据库：隔离库 `aitkp_embedding`（PostgreSQL 17 + pgvector 0.8.7）
- 数据集：`eval/fixtures/phase6_simulated.json`
- 数据集 SHA-256：`63f0f6a4974daa6b6c904119d21b3bc3ce0f2cb5cffed3a54ed1291336c1999c`
- 固定切分：Phase 6 最优基线 `entity+heading`，46 个 Chunk
- 原始 manifest：`results/embedding-phase7.json`

## 1. 实验目标

固定语料、Query、切分和检索实现，只替换 Embedding 模型，比较：

- Recall@5 / Recall@10；
- MRR；
- 不可回答 Query 的 Top-K 误召回；
- 内容向量化、写库、Query Embedding 与 pgvector 检索延迟。

本实验回答“在相同检索单元上，哪个模型更适合当前中文测试知识语料”，
不评价 LLM 生成质量，也不比较混合检索或 Rerank。

## 2. 为什么固定 `entity+heading`

Phase 6 在相同 40 条 Query 上得到：

| 策略 | Chunks | Recall@5 | MRR |
|---|---:|---:|---:|
| semantic | 43 | 0.975 | 0.919 |
| entity+heading | 46 | 0.966 | **0.936** |

`entity+heading` 的 Recall@5 只低约 0.009，但 MRR 更高，且切分阶段不调用
Embedding，准备成本更低。因此 Phase 7 固定它作为基线。否则同时改变切分与
Embedding，指标变化无法归因。

## 3. 模型与隔离策略

| 报告标识 | OMLX 模型 | 维度 |
|---|---|---:|
| `bge-m3-mlx-4bit` | `bge-m3-mlx-4bit` | 1024 |
| `embeddinggemma-2-oQ8e` | `embeddinggemma-2-oQ8e` | 768 |
| `all-MiniLM-L6-v2` | `all-MiniLM-L6-v2` | 384 |

生产表 `knowledge_chunks.embedding` 固定为 `Vector(1024)`。三个模型维度不同，
不能写入同一列后截断或补零，否则向量空间会被破坏。因此每个模型写入独立实验表：

| 模型 | 实验表 |
|---|---|
| `bge-m3-mlx-4bit` | `phase7_embedding_bge_m3_mlx_4bit_1024` |
| `embeddinggemma-2-oQ8e` | `phase7_embedding_embeddinggemma_2_oq8e_768` |
| `all-MiniLM-L6-v2` | `phase7_embedding_all_minilm_l6_v2_384` |

脚本只允许写入库名包含 `embedding` 的 PostgreSQL 数据库，并检查 `vector`
扩展。本次三张表各 46 行、46 个唯一来源组合，向量维度分别为 1024 / 768 / 384；
开发库 `ai_test_knowledge` 未被写入。

## 4. 结果

### 4.1 检索质量

| 模型 | 维度 | Chunks | Recall@5 | Recall@10 | MRR |
|---|---:|---:|---:|---:|---:|
| `bge-m3-mlx-4bit` | 1024 | 46 | **0.966** | 0.985 | **0.936** |
| `embeddinggemma-2-oQ8e` | 768 | 46 | 0.941 | 0.985 | 0.934 |
| `all-MiniLM-L6-v2` | 384 | 46 | 0.466 | 0.520 | 0.447 |

34 条可回答 Query 共 59 条标注证据：

- `bge-m3` 在 Recall@5 / Recall@10 命中 56 / 58 条，MRR 0.936；
- `embeddinggemma` 命中 53 / 58 条，MRR 0.934；
- `all-MiniLM-L6-v2` 只命中 21 / 26 条，MRR 0.447。

`bge-m3` 与 `embeddinggemma` 的 MRR 只差约 0.002，当前样本不足以宣称两者有稳定
显著差异；`all-MiniLM-L6-v2` 的差距则足够大，表明它不适合作为这套中文测试知识
语料的主 Embedding，不能只因向量更短、调用更快就选它。

### 4.2 不可回答 Query

| 模型 | 误召回率 | top1 distance 最小值 | top1 distance 中位数 |
|---|---:|---:|---:|
| `bge-m3-mlx-4bit` | 1.000 | 0.280 | 0.384 |
| `embeddinggemma-2-oQ8e` | 1.000 | 0.206 | 0.241 |
| `all-MiniLM-L6-v2` | 1.000 | 0.266 | 0.311 |

三种模型都让 6 条不可回答 Query 在 Top-10 中返回了内容。测试数量太少，
不能从这些 distance 直接选生产 `max_distance`；但可以看到不同模型的距离分布
不可互换，阈值必须按最终选定模型单独校准。

### 4.3 成本与延迟

| 模型 | 内容 Embedding | 写库 | 索引合计 | Query Embedding P50 / P95 | pgvector P95 | 端到端 P95 |
|---|---:|---:|---:|---:|---:|---:|
| `bge-m3-mlx-4bit` | 1.159s | 0.040s | 1.199s | 0.024s / 0.028s | 0.005s | 0.034s |
| `embeddinggemma-2-oQ8e` | 0.614s | 0.034s | 0.648s | 0.013s / 0.023s | 0.005s | 0.030s |
| `all-MiniLM-L6-v2` | 0.268s | 0.021s | 0.289s | 0.007s / 0.010s | 0.005s | 0.016s |

表中的“索引合计”= 内容 Embedding + 写库，不含建表与首次 warmup。
46 个 Chunk 的绝对耗时很短，主要反映本机 OMLX 的单次批处理开销和当前负载；
pgvector 在 46 行数据上约为毫秒级，差异没有工程意义。

本实验的检索表没有建立 HNSW 索引，所有模型使用相同的精确距离扫描。因此这里
**只比较模型语义质量与调用成本，不比较 HNSW 参数**；数据量扩大后需要单独评测
索引构建时间、召回损失和查询延迟。

## 5. 解释与决策

1. **`bge-m3-mlx-4bit` 保持默认**：当前 MRR 最高，Recall@5 也最高；与
   `embeddinggemma` 的差距很小，但没有证据支持为了约 2 倍的离线 Embedding
   速度牺牲默认配置的兼容性与已验证结果。
2. **`embeddinggemma-2-oQ8e` 是合理候选**：质量接近、维度更小、调用更快；
   若后续大语料上的 Recall / MRR 不下降，可重新评估为低成本默认项。
3. **`all-MiniLM-L6-v2` 当前淘汰**：Recall@5 只有 0.466，远低于另外两者；
   低延迟不能弥补检索错误。
4. **不能外推到生产规模**：本实验只有 3 篇模拟文档、40 条 Query、46 个 Chunk，
   结论是可复现基线，不是统计显著的生产选型。

## 6. 复现

先创建隔离库并安装扩展（不会清空开发库）：

```bash
docker compose exec postgres psql -U postgres -d postgres \
  -c 'CREATE DATABASE aitkp_embedding'
docker compose exec postgres psql -U postgres -d aitkp_embedding \
  -c 'CREATE EXTENSION IF NOT EXISTS vector'
```

然后运行：

```bash
uv run python scripts/embedding_experiment.py \
  --database-url 'postgresql+psycopg://USER:PASSWORD@127.0.0.1:5432/aitkp_embedding' \
  --output results/embedding-phase7.json
```

也可以先做无副作用的配置校验：

```bash
uv run python scripts/embedding_experiment.py --dry-run
```

脚本会用 `Base.metadata.create_all` 确保隔离库具备基础实体表，再为每个模型
重建模型专属向量表。manifest 记录数据集 SHA-256、起止时间、`git_commit` /
`git_dirty`、脱敏后的命令、模型与维度、Chunk 统计和完整指标。

## 7. 已知局限

- 语料和 Query 为模拟数据，规模小，高 Recall 不能直接外推到真实知识库。
- Gold Set 以“包含证据片段”判断命中，可能高估完整回答所需证据的召回。
- 只评测 Vector Search；Keyword、Hybrid、Rerank 的影响尚未纳入。
- 未评测 Precision@K、NDCG、生成 Faithfulness 和 Citation Accuracy。
- 延迟受本机 OMLX 冷启动、批大小和机器负载影响，不是服务端 SLA。
- 实验表未建 HNSW，不能据此判断不同维度下 ANN 索引的表现。
- 模型距离分布不同，不可回答 Query 的阈值不能跨模型复用。

## 8. 下一步

Phase 8 进入 BM25 / Full Text Search，先解决中文精确编号、版本号和关键词召回，
再在 Phase 9 与当前 Vector 结果融合。扩大 Gold Set、补 Precision@K / NDCG 与
HNSW 参数对比应作为并行评测任务，而不是混入 Phase 8 的实现提交。
