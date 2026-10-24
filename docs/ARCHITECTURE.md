# Architecture：系统是如何组织的？

- 日期：2026-10-24
- 分支：`phase-12-evaluation-dataset`
- 回答的问题：一个测试知识请求从进入系统到返回答案，依次经过哪些组件，每个组件的职责边界是什么？

## 1. 请求路径

```text
Question
→ Query Understanding (app/query/understanding.py)
→ Query Rewrite (app/query/rewrite.py)
→ Retrieval: Vector + BM25 + Metadata Filter (app/retrieval/)
→ Fusion (app/retrieval/hybrid.py)
→ Rerank 可选 (app/rerank/)
→ Context Assembly (app/generation/context.py)
→ LLM + Citation (app/generation/rag.py)
→ RAGResponse + 逐阶段 trace (app/generation/pipeline.py)
```

Phase 18 起完整链路由 `app/generation/pipeline.py` 组合；
`app/api/rag.py` 的公开端点当前仍走 Phase 3 最小 RAG，Pipeline 尚未替换默认路径。

## 2. 数据流与存储

```text
CSV / Excel / Markdown / PDF
→ app/ingestion/ (Parser → Normalizer → Chunker)
→ documents + knowledge_chunks (content_hash 预留增量索引)
→ Embedding 由 scripts/index_entities.py 显式生成
```

- 权限字段尚未建模；`app/retrieval/access_control.py` 只提供候选层契约。
- 版本语义在 `app/retrieval/versioning.py`，区间过滤尚未接到 SQL。

## 3. 评测体系

| 层 | 入口 | 产物 |
|---|---|---|
| 检索指标 | `app/evaluation/metrics.py` | `results/retrieval-phase13.json` |
| 生成指标 | `app/evaluation/generation_evaluation.py` | `results/generation-phase14.json` |
| 候选数据集 | `app/evaluation/phase12_dataset.py` | `eval/fixtures/phase12_candidates.json`（全部 pending） |
| Query 理解 / 改写 | `app/evaluation/query_rewrite_evaluation.py` | `results/query-*.json` |
| Badcase 闭环 | `app/evaluation/badcase.py` | 进程内，未持久化 |
| 性能统计 | `app/evaluation/performance.py` | 库级契约 |
| Dashboard 聚合 | `app/evaluation/dashboard.py` | manifest 投影 |
| 实验矩阵 | `app/evaluation/experiment_matrix.py` | manifest 投影 |

## 4. 硬边界

1. AI 只能生成 `pending` 候选；formal 模式要求 `review_status=confirmed`
   且 `reviewed_by=Terry`（`app/evaluation/phase12_dataset.py`）。
2. `citation_valid` 只表示引用编号合法；忠实性由 Phase 14 离线评测。
3. 所有 manifest 数字都是 candidate 模式；不能当成生产结论。
4. SQLite 路径用精确 cosine + BM25，不伪装 pgvector 结果。
5. Agent 工具是证据唯一入口；Planner 不能绕过工具直接生成答案
   （`app/agent/tools.py`）。

## 5. 更多细节

- 数据模型：`docs/PHASE4_KNOWLEDGE_MODEL.md`
- 检索实验：`docs/CHUNKING_EXPERIMENT.md`、`docs/HYBRID_SEARCH_EXPERIMENT.md`
- 评测口径：`docs/RETRIEVAL_EVALUATION.md`、`docs/GENERATION_EVALUATION.md`
- 进度总览：`docs/PHASE_PROGRESS.md`
