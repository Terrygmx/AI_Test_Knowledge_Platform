# Embedding 实验：本机 OMLX bge-m3

- 日期：2026-10-20
- 服务：OMLX，`http://127.0.0.1:28080/v1`
- 模型：`bge-m3-mlx-4bit`
- 维度：1024
- Key：`omlx`（本机占位值）

## 1. 相似度探针

命令：

```bash
uv run python scripts/embedding_probe.py --pairs
```

结果：

| 文本对 | cosine similarity |
|---|---:|
| 支付失败 / 支付错误 | 0.9058 |
| 支付失败 / 登录成功 | 0.6496 |
| 登录失败 / 无法登录 | 0.9127 |
| P0 支付缺陷 / 支付问题 | 0.6992 |

结论：同义 / 近义表达明显高于无关表达，说明本机 OMLX 的 bge-m3
链路和语义区分能力正常。该结果只能证明模型可用，不能替代 Recall@K / MRR。

## 2. 真实索引与 pgvector 检索

写入 2 条 TestCase 和 1 条 Defect，执行：

```bash
uv run python scripts/index_entities.py --only testcase,defect
```

结果：`testcase=2, defect=1`，3 条 Chunk 全部带 1024 维向量。

查询结果：

| Query | Top1 | Distance |
|---|---|---:|
| 登录失败怎么测试 | `testcase:LOGIN-OMLX-001` | 0.3143 |
| 支付失败怎么处理 | `testcase:PAY-OMLX-001` | 0.2675 |

两个查询的 Top1 均命中对应领域知识，说明真实 Embedding → pgvector 链路可用。

## 3. 边界

- 本次样本是链路验证数据，不是人工标注 Gold Set。
- 尚未计算 Recall@5、MRR、Citation Accuracy、Faithfulness。
- 相关性阈值 `max_distance` 仍需用真实评测集校准，不能根据本报告直接确定。
