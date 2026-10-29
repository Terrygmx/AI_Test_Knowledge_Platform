# Tasks：AI Test Knowledge Platform 任务清单

- 文档状态：生效（v1.0，2026-10-29）
- 执行规则：按依赖顺序逐项完成；上一项验收未通过，不进入下一项。
- 状态口径：`已完成` / `进行中` / `阻塞` / `未开始`。
- 证据要求：每项必须写测试、命令输出或报告路径；没有证据不得标为已完成。

## 状态总览

| ID | 任务 | 依赖 | 状态 |
|---|---|---|---|
| T0 | SDD 规范基线 | — | 已完成 |
| T1 | 项目边界与 API/DB 基座 | T0 | 已完成 |
| T2 | Embedding 与 pgvector 检索 | T1 | 已完成 |
| T3 | 最小 RAG 与 Citation 校验 | T2 | 已完成 |
| T4 | 知识模型与数据接入 | T1 | 已完成 |
| T5 | Chunking / Embedding / BM25 / Hybrid / Filter 实验 | T4 | 已完成（小样本） |
| T6 | Rerank 接缝 | T5 | 已完成（正式实验阻塞） |
| T7 | Phase 12 评测数据集契约 | T4 | 已完成（候选） |
| T8 | 检索评测指标与 Badcase | T7 | 已完成（candidate） |
| T9 | 生成评测指标 | T7 | 已完成（candidate） |
| T10 | Query Understanding / Rewrite | T7 | 已完成（candidate） |
| T11 | Citation 增强与完整 Pipeline | T3、T10 | 已完成 |
| T12 | Badcase / 性能 / 增量 / 版本 / 权限 | T8、T9 | 已完成 |
| T13 | Dashboard / Agent / 回归 / 矩阵 / 架构文档 | T11、T12 | 已完成 |
| T14 | 七项工程缺口收口 | T12、T13 | 已完成 |
| T15 | Phase 12 的 100 条 Query 人工确认 | T7 | 阻塞（人工） |
| T16 | formal 检索 / 生成评测重跑 | T15 | 阻塞 |
| T17 | Reranker 选型与正式实验 | T6 | 阻塞（人工 + 服务） |
| T18 | SLO 定义与真实压测 | T12 | 阻塞（人工） |
| T19 | CI 远程首跑验证 | T14 | 阻塞（需 push） |
| T20 | Phase 30 最终验收 | T16–T19 | 阻塞 |

---

## T0 SDD 规范基线

**依赖**：无。

**交付物**

- `docs/sdd/proposal.md`
- `docs/sdd/design.md`
- `docs/sdd/api-spec.yaml`
- `docs/sdd/tasks.md`
- `app/api/contract.py`、`scripts/export_openapi.py`、`tests/test_sdd_api_spec.py`

**验收标准**

- 四份文档齐备，内容与当前实现一致。
- `api-spec.yaml` 与运行中 OpenAPI 完全一致，且每个操作声明统一错误信封。
- `uv run python scripts/export_openapi.py --check` 退出码 0。

**状态**：已完成

**证据**：`tests/test_sdd_api_spec.py`；契约文件由实现导出（非手写）。

---

## T1 项目边界与 API/DB 基座

**依赖**：T0。

**覆盖 Phase**：0、1。

**交付物**

- `docs/PROJECT_SCOPE.md`、`docs/LEARNING_QUESTIONS.md`、实现计划。
- FastAPI 应用工厂、四实体 CRUD、统一错误信封、请求追踪中间件。
- Alembic `0001` 初始 Schema、PostgreSQL 实机验证。

**验收标准**

- 四类实体可创建 / 查询 / 更新 / 删除。
- 非法请求返回统一错误信封；500 不回显内部细节。
- `alembic upgrade head` 与 `alembic downgrade base` 可执行。

**状态**：已完成

**证据**：`tests/api/`、`tests/test_migrations.py`、`alembic/versions/0001_initial_schema.py`。

---

## T2 Embedding 与 pgvector 检索

**依赖**：T1。

**覆盖 Phase**：2。

**交付物**

- `EmbeddingClient` 抽象、bge-m3 探针、1024 维校验。
- `knowledge_chunks` 表、HNSW cosine 索引、向量检索服务、索引入口。

**验收标准**

- 维度不一致的向量在写入前被拒绝。
- PostgreSQL 上按 cosine distance 返回稳定排序。
- SQLite 上明确报错，不伪装向量检索。

**状态**：已完成

**证据**：`tests/services/test_knowledge_chunk_service.py`、`alembic/versions/0002_knowledge_chunks.py`、`docs/EMBEDDING_EXPERIMENT.md`。

---

## T3 最小 RAG 与 Citation 校验

**依赖**：T2。

**覆盖 Phase**：3。

**交付物**

- Context Assembly、Prompt 构建、LLM 调用、引用编号校验、无证据拒答。
- 同步接口的超时（503）与并发闸门（429）。

**验收标准**

- 引用编号越界时整体拒绝回答。
- 知识库为空时返回拒答，不产生幻觉。
- 超时在预算内返回，工作线程不阻塞请求。

**状态**：已完成

**证据**：`tests/test_rag.py`、`tests/api/test_rag_api.py`、`app/generation/rag.py`。

---

## T4 知识模型与数据接入

**依赖**：T1。

**覆盖 Phase**：4、5。

**交付物**

- 四实体字段契约、业务编号与 Metadata 映射。
- CSV / Excel / Markdown / 纯文本 PDF 解析、幂等导入、文档 Chunk。

**验收标准**

- 重复导入同一文件不产生重复 Chunk。
- 空文档不清空既有 Chunk，并如实报告。
- 导入与 Embedding 解耦，提示补向量入口。

**状态**：已完成

**证据**：`tests/test_ingestion.py`、`tests/test_ingest_script.py`、`docs/PHASE5_INGESTION.md`。

---

## T5 检索策略实验

**依赖**：T4。

**覆盖 Phase**：6、7、8、9、10。

**交付物**

- Chunking 六策略对比、Embedding 三模型对比。
- BM25 词法检索、Hybrid（Weighted / RRF）、Metadata Filter DSL。
- 可复现 manifest 与报告。

**验收标准**

- 实验固定其他变量，只改目标变量。
- 过滤在 SQL `WHERE` 中执行（LIMIT 前），SQLite / PG 语义一致。
- 报告显式标注小样本局限。

**状态**：已完成（小样本）

**证据**：`results/chunking-2026-10-20.json`、`results/embedding-phase7.json`、
`results/lexical-phase8.json`、`results/hybrid-phase9.json`、
`results/metadata-filter-phase10.json`、`tests/test_metadata_filters.py`。

---

## T6 Rerank 接缝

**依赖**：T5。

**覆盖 Phase**：11。

**交付物**

- OpenAI-compatible `/v1/rerank` 客户端、预检、重试、短响应契约。
- 同 run 输出 `rrf` 与 `rrf_rerank` 双通道。

**验收标准**

- 预检发生在建库 / Embedding 之前。
- provider 返回少于 `top_n` 的合法子集可接受；空集报错。
- 正式报告在可用 reranker 下生成（当前阻塞）。

**状态**：已完成（正式实验阻塞）

**证据**：`tests/test_hybrid_rerank_experiment.py`、`scripts/rerank_experiment.py`。

---

## T7 Phase 12 评测数据集契约

**依赖**：T4。

**覆盖 Phase**：12。

**交付物**

- 独立 `Phase12Dataset` Schema、六类配额、证据 / 过滤条件校验。
- 100 条 AI 候选（全部 `pending`）、审阅辅助工具。

**验收标准**

- 证据必须真实出现在语料中；Gold 文档 / Chunk 一致。
- `confirmed` / `rejected` 必须带 `reviewed_by=Terry`、时区时间、备注。
- `formal` 模式拒绝任何未确认条目。

**状态**：已完成（候选）

**证据**：`tests/test_phase12_dataset.py`、`tests/test_phase12_review.py`、
`eval/fixtures/phase12_candidates.json`、`scripts/phase12_review.py`。

---

## T8 检索评测指标与 Badcase

**依赖**：T7。

**覆盖 Phase**：13。

**交付物**

- Recall@K / Precision@K / MRR / NDCG、逐 Query Badcase、candidate / formal 模式。

**验收标准**

- 重复命中不重复计分；NDCG 不超过 1。
- 同输入同结果哈希，可做回归对比。
- formal 模式拒绝 pending 数据。

**状态**：已完成（candidate）

**证据**：`tests/test_retrieval_evaluation.py`、`results/retrieval-phase13.json`。

---

## T9 生成评测指标

**依赖**：T7。

**覆盖 Phase**：14。

**交付物**

- Faithfulness、Citation Accuracy、Refusal Correct、Answer Relevancy（启发式）、
  Context Recall / Precision、检索错误与生成错误归因。

**验收标准**

- 拒答句视为受支持；混合回答不整体豁免。
- 引用编号逐个校验，准确率 = 合法编号 / 全部编号。
- 检索正确但答案缺要点归因为生成错误。

**状态**：已完成（candidate）

**证据**：`tests/test_generation_evaluation.py`、`results/generation-phase14.json`。

---

## T10 Query Understanding / Rewrite

**依赖**：T7。

**覆盖 Phase**：15、16。

**交付物**

- 结构化查询 Schema、确定性解析、LLM JSON 严格校验与降级。
- NoOp / Deterministic / LLM / Composite 改写策略与对照实验。

**验收标准**

- LLM 输出未知字段 / 多 JSON 块被拒绝。
- 失败时显式降级并记录原因。
- 改写必须与 noop 对照；无变化时不得宣称收益。

**状态**：已完成（candidate）

**证据**：`tests/test_query_understanding.py`、`tests/test_query_rewrite.py`、
`results/query-rewrite-phase16.json`、`docs/QUERY_UNDERSTANDING_REWRITE.md`。

---

## T11 Citation 增强与完整 Pipeline

**依赖**：T3、T10。

**覆盖 Phase**：17、18。

**交付物**

- Citation `chunk_id` / `score`、`source_lines()`。
- 完整 Pipeline 与逐阶段 trace；可选 HTTP 端点。

**验收标准**

- 每个阶段可 JSON 化追溯（understanding / retrieval / rerank / context / llm）。
- Pipeline 端点共享并发与超时契约，不替换默认 `/rag/answer`。

**状态**：已完成

**证据**：`tests/test_rag_pipeline.py`、`tests/api/test_rag_api.py`、
`review/review-step1-pipeline-api-20261024-1610.md`。

---

## T12 Badcase / 性能 / 增量 / 版本 / 权限

**依赖**：T8、T9。

**覆盖 Phase**：19、20、21、22、23。

**交付物**

- Badcase 九阶段分类、状态机、持久化表（迁移 0003）与 API。
- 分位数统计契约；增量索引计划器 + 执行器 + CLI。
- `version_key`（迁移 0004）与版本区间 SQL 过滤；权限 SQL pre-filter。

**验收标准**

- verified 必须附带评测结果；重复 badcase_id 返回 409。
- 全删场景 DELETE 必须被检测；复跑幂等。
- `v2.10` 按数值大于 `v2.9`；无版本不匹配。
- 权限畸形配置 fail-closed；SQLite / PG 语义一致。

**状态**：已完成

**证据**：`tests/test_badcase_persistence.py`、`tests/test_incremental_index_service.py`、
`tests/test_version_access_filters.py`、`review/review-step2..4-*.md`。

---

## T13 Dashboard / Agent / 回归 / 矩阵 / 架构文档

**依赖**：T11、T12。

**覆盖 Phase**：24、25、26、27、28。

**交付物**

- manifest 聚合投影（保留 candidate / formal 溯源与缺失标记）。
- Agent 工具白名单、受限循环、真实检索工具、回归计划人工批准门槛。
- 实验矩阵、架构总览。

**验收标准**

- 聚合不把 candidate 结果升级为 formal；缺失实验显式标记。
- Agent 工具是唯一证据入口，证据带 `source_type:source_id`。
- 回归计划未批准不可执行。

**状态**：已完成

**证据**：`tests/test_agent_tools.py`、`tests/test_agent_retrieval_tools.py`、
`docs/ARCHITECTURE.md`、`review/review-step7-agent-retrieval-tools-20261024-1800.md`。

---

## T14 七项工程缺口收口

**依赖**：T12、T13。

**交付物**

- Pipeline HTTP API、Badcase 持久化、增量执行器、版本/权限 SQL pre-filter、
  Phase 12 审阅工具、CI PostgreSQL 门禁、Agent 真实检索。

**验收标准**

- 每项有独立 review 与回归测试。
- SQLite 与 PostgreSQL 全量测试通过；`alembic check` 通过。
- CI 配置包含 PG service、迁移与全量测试。

**状态**：已完成

**证据**：`review/review-step1..7-*.md`；SQLite 733 passed / 17 skipped；
PostgreSQL 748 passed / 2 skipped；`ruff` / `git diff --check` / `alembic check` 通过。

---

## T15 Phase 12 的 100 条 Query 人工确认

**依赖**：T7。

**交付物**

- Terry 逐条填写的审阅结果（confirmed / rejected + review_notes）。
- 回填后的数据集文件（不覆盖候选原文件）。

**验收标准**

- 100 条全部有审阅结论；confirmed 条目证据链校验通过。
- `uv run python scripts/phase12_candidates.py --check --mode formal` 退出码 0。

**状态**：阻塞（人工）

**证据**：当前 `scripts/phase12_review.py progress` 显示 100 pending。

**操作入口**

```bash
uv run python scripts/phase12_review.py export --output eval/fixtures/phase12_review_checklist.json
# Terry 编辑 checklist 的 decision / review_notes
uv run python scripts/phase12_review.py apply \
  --checklist eval/fixtures/phase12_review_checklist.json \
  --output eval/fixtures/phase12_goldset.json \
  --mode candidate
uv run python scripts/phase12_candidates.py \
  --dataset eval/fixtures/phase12_goldset.json --check --mode formal
```

---

## T16 formal 检索 / 生成评测重跑

**依赖**：T15。

**交付物**

- `retrieval_evaluation.py --mode formal` 与 `generation_evaluation.py --mode formal` 报告。
- 与 candidate 结果的对照结论。

**验收标准**

- 报告记录数据集哈希、模式、参数与逐样本明细。
- 达到或未达到 proposal 6.2 的目标都有明确结论，不使用模拟响应。

**状态**：阻塞

**证据**：待 T15 完成。

---

## T17 Reranker 选型与正式实验

**依赖**：T6。

**交付物**

- 可用 reranker 的预检通过记录。
- 同 run 的 `rrf` vs `rrf_rerank` Recall@5 / MRR / 延迟报告。

**验收标准**

- provider 稳定返回合法子集；空响应有明确错误。
- 报告显式标注候选深度、top_n、模型与成本。

**状态**：阻塞（人工 + 服务）

**证据**：默认模型当前预检 409，未生成正式报告。

---

## T18 SLO 定义与真实压测

**依赖**：T12。

**交付物**

- SLO 文档（P95、最大并发、错误预算）。
- 压测脚本与分阶段延迟报告。

**验收标准**

- 使用真实 PG + 模型服务；报告含 P50/P95/P99。
- 结论与 `proposal.md` 6.2 对照。

**状态**：阻塞（人工定义 SLO）

**证据**：当前只有统计契约，无压测数据。

---

## T19 CI 远程首跑验证

**依赖**：T14。

**交付物**

- 远程 CI 两个 job 的绿色运行记录。

**验收标准**

- SQLite job 与 PostgreSQL job 均通过。
- `alembic check` 在 CI 中通过。

**状态**：阻塞（需 push 触发）

**证据**：`.github/workflows/ci.yml` 已就绪，本地等价演练通过。

---

## T20 Phase 30 最终验收

**依赖**：T16、T17、T18、T19。

**交付物**

- 最终验收报告：功能 / 接口 / 架构 / 性能 / 质量五类结论。

**验收标准**

- 对照 `proposal.md` 第 7 节逐项签署。
- 未达标项有明确原因与后续计划，不以 candidate 数据替代。

**状态**：阻塞

**证据**：待前置任务完成。

---

## 变更记录

| 日期 | 变更 | 说明 |
|---|---|---|
| 2026-10-29 | 初版 | 建立 SDD 基线，回填 T1–T14 证据与 T15–T20 阻塞项 |
