# RAG 实验：本机 OMLX 端到端链路

- 日期：2026-10-20
- 分支：`phase-3-llm`
- Embedding：OMLX `bge-m3-mlx-4bit`（1024 维）
- LLM：OMLX `Ling-3.0-tiny-oQ6e`
- 服务地址：`http://127.0.0.1:28080/v1`
- 数据库：PostgreSQL 17 + pgvector，Alembic `0002 (head)`
- 索引数据：2 条 TestCase、1 条 Defect，共 3 个 Chunk，全部有向量

## 1. 健康检查

`GET /health` 返回：

```text
embedding=true
llm=true
vector_search=true
rerank=false
chunks_without_embedding=0
```

Rerank 尚未接入，符合当前 Phase 3 边界。

## 2. 命令行端到端验证

命令：

```bash
uv run python scripts/ask.py "登录失败怎么测试？"
uv run python scripts/ask.py "支付失败怎么处理？"
uv run python scripts/ask.py "今天北京天气如何？"
uv run python scripts/ask.py --max-distance 0.2 "登录失败怎么测试？"
```

结果：

| Query | 结果 | 引用 | 说明 |
|---|---|---|---|
| 登录失败怎么测试？ | 给出验证码登录与缓存过期两类测试方法 | `testcase:LOGIN-OMLX-001`、`defect:BUG-OMLX-001` | 两条引用均合法 |
| 支付失败怎么处理？ | 给出重试与不重复扣款的预期 | `testcase:PAY-OMLX-001` | 引用合法 |
| 今天北京天气如何？ | “证据不足” | 无 | 未伪造外部知识 |
| 登录失败怎么测试？ + `max_distance=0.2` | 直接拒答 | 无 | 所有候选距离均大于阈值，未调用 LLM |

登录查询的 Top3 cosine distance 为 `0.3112 / 0.3357 / 0.3719`；
支付查询为 `0.2691 / 0.3651 / 0.4224`；无关天气查询为
`0.6528 / 0.6529 / 0.6659`。这组样本能区分相关与无关问题，但不能替代
Gold Set 上的 Recall@K / MRR。

## 3. HTTP API 验证

`POST /api/v1/rag/answer` 返回：

- 支付问题：`citation_valid=true`，Citation 为 `PAY-OMLX-001`，
  `rank=1`，`distance≈0.2691`，耗时约 11.0 秒。
- 天气问题：`citation_valid=false`，`evidence_sufficient=false`，
  `citations=[]`，耗时约 11.8 秒。

## 4. 真实模型暴露的问题与修复

`Ling-3.0-tiny-oQ6e` 会返回 `reasoning_content`，且可能在给出两条带引用结论后
追加“证据不足”。旧逻辑看到任意“证据不足”就整条拒答，导致合法引用被丢弃。

修复后规则：

1. 先解析引用；存在合法引用时保留部分回答。
2. 只有没有任何引用、且回答明确表示“证据不足”时，才返回拒答。
3. 引用编号超出本次证据范围仍然报错，不放宽 Citation 校验。

对应回归测试：
`tests/test_rag.py::TestRAGService::test_partial_answer_keeps_citations_when_model_also_mentions_gap`。

## 5. 延迟与边界

- 单次真实 RAG 请求约 11–60 秒，主要耗时在 LLM 推理；该模型会先生成
  `reasoning_content`，相同问题也可能因推理长度不同而波动。
- 本次只验证链路正确性，不代表 Faithfulness / Citation Accuracy 已达到目标。
- `citation_valid` 仍只表示引用编号合法，不表示答案内容忠实于证据。
- `max_distance` 仍需在人工 Gold Set 上校准，不能把本次 `0.2` 当作生产阈值。
- 下一阶段应建立约 100 条人工确认 Query，并计算 Recall@K / MRR。
