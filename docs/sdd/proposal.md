# Proposal：AI Test Knowledge Platform

- 文档状态：生效（v1.0，2026-10-29）
- 关联文档：`docs/PROJECT_SCOPE.md`、`docs/PHASE_PROGRESS.md`、`docs/sdd/design.md`
- 变更规则：范围、成功标准或验收口径变化时，先改本文，再改 `tasks.md` 与实现。

## 1. 背景与问题

测试知识分散在测试用例、缺陷、需求/产品文档和测试规范中。工程实践里常见两类失败：

1. **检索失败**：关键词搜不到语义相近的内容；编号、版本号、模块等硬条件无法组合过滤。
2. **生成失败**：模型给出了看似合理但无来源的答案，或引用了不存在的 Case ID / Bug ID。

本项目要回答的核心问题：

> 如何让测试人员和智能测试 Agent 在**可追溯**的前提下，快速找到覆盖当前版本、
> 模块、优先级和历史缺陷的正确证据，并基于证据给出可核验的回答？

典型问题：

- 登录模块 P0 的短信登录冒烟用例有哪些？
- 支付失败相关的高优先级缺陷最近如何修复？
- V1.3 支付模块上线前应回归哪些用例和需求？
- V1.2 到 V1.3 的支付测试范围发生了什么变化？

## 2. 目标

### 2.1 产品目标

- 提供跨 TestCase / Defect / Requirement / Document 四类来源的统一检索。
- 支持“语义相似 + 关键词 + 结构化 Metadata”组合检索。
- 所有回答必须带可追溯引用；证据不足时明确拒答。
- 提供可复现的离线评测体系，区分 candidate（候选）与 formal（人工确认）结论。

### 2.2 学习目标

通过 30 个 Phase 的渐进式实现，让学习者掌握 RAG 全链路：
数据接入 → Chunking → Embedding → 词法/向量检索 → 融合 → 过滤 → 精排 →
Query 理解/改写 → 上下文组装 → 生成与引用 → 评测 → Badcase 闭环 → 工程化。

## 3. 非目标（当前阶段明确不做）

- 不引入 Milvus 等多套向量存储；PostgreSQL + pgvector 单库承担关系、Metadata、
  全文与向量检索。
- 不做 OCR、复杂版面还原、扫描件解析。
- 不使用 LangChain / LlamaIndex 隐藏检索链路；核心 Pipeline 手写。
- 不让 LLM 直接决定最终证据或生成引用编号。
- 不训练或微调自有 Embedding / LLM。
- 不做开放域聊天机器人；生成必须锚定检索证据。
- 不在 Retrieval 稳定前投入大量 UI；Dashboard 当前只做 manifest 聚合。
- 不实现完整用户体系与登录；权限以检索层契约为 MVP。
- 不把候选数据、模拟数据或小样本结果包装成生产结论。

## 4. 用户与场景

| 用户 | 场景 | 期望结果 |
|---|---|---|
| 测试工程师 | 查用例/缺陷/需求/规范 | 结果带来源、可按模块/版本/优先级过滤 |
| 测试负责人 | 评估版本回归范围 | 跨版本证据 + 可解释的检索结果 |
| 智能回归 Agent | 通过稳定接口获取证据 | 结构化、可追溯、失败可分类 |
| 项目学习者 | 学习 RAG 全链路 | 每阶段有可运行代码、实验与自测方法 |

## 5. 范围

### 5.1 MVP 必须支持

1. 四类业务实体 CRUD 与统一错误信封。
2. CSV / Excel / Markdown / 纯文本 PDF 接入与幂等导入。
3. Chunk 化、Embedding、pgvector cosine 检索。
4. BM25 词法检索与 Hybrid（Weighted / RRF）融合。
5. 受控 Metadata Filter（白名单字段、SQL 预过滤）。
6. Rerank 接缝（OpenAI-compatible `/v1/rerank`）。
7. 最小 RAG 与完整 Pipeline（Understanding → Hybrid → Rerank → LLM），带逐阶段 trace。
8. Citation（来源编号、Chunk ID、score、来源清单）。
9. 评测：Gold Set 契约、Recall@K / Precision@K / MRR / NDCG、
   Faithfulness / Citation Accuracy / Refusal Correct、Badcase 归因。
10. 工程能力：Badcase 持久化、增量索引、版本区间 SQL 过滤、检索层权限过滤、
    性能分位数、实验矩阵、Agent 工具白名单。

### 5.2 MVP 之外（Roadmap）

- 真实生产数据的大规模验收与容量规划。
- 正式 Reranker 选型与成本结论。
- Ragas 作为正式生成评测后端（当前仅可选占位）。
- LLM Planner / 多轮 Agent 反思。
- 完整前端 Dashboard。

## 6. 成功标准

### 6.1 功能成功标准

- 四类实体可通过 API 完成创建、查询、更新、删除；错误统一返回
  `{"error": {code, message, request_id, detail}}`。
- 一次 RAG 请求的每个阶段都可在响应 trace 中定位（understanding / retrieval /
  rerank / context / llm）。
- 任意回答中的引用编号都能映射到本次检索到的真实证据；无证据时拒答。
- Metadata Filter 在 SQL `WHERE` 中执行（LIMIT 之前），SQLite 与 PostgreSQL 语义一致。
- 版本区间过滤按数值语义生效（`v2.10 > v2.9`），无版本/非法版本不匹配。
- 权限过滤在检索层完成，畸形权限配置 fail-closed。
- Phase 12 数据集结构、证据链与审阅来源校验全部通过；`formal` 模式拒绝未确认数据。

### 6.2 质量成功标准（初始目标，需人工 Gold Set 校准）

| 指标 | 初始目标 | 当前状态 |
|---|---:|---|
| Recall@5 | ≥ 85% | 仅有 candidate 结果，未验收 |
| MRR | ≥ 0.80 | 仅有 candidate 结果，未验收 |
| Citation Accuracy | ≥ 90% | 模拟响应结果，未验收 |
| Faithfulness | ≥ 90% | 模拟响应结果，未验收 |
| P95 Latency | < 5s | 未做真实压测，未验收 |

> 质量目标只有在 Phase 12 的 100 条 Query 全部由人工确认（`reviewed_by=Terry`）
> 并以 `formal` 模式重跑后，才能判定通过或不通过。

### 6.3 工程成功标准

- 默认测试不依赖 Docker 与外部模型服务；PostgreSQL 专属能力有真实 PG 测试。
- `ruff`、全量 pytest、`git diff --check`、`alembic check` 全部通过。
- CI 含 PostgreSQL + pgvector job（空库迁移 → head → 全量测试）。
- 每个 Phase 有 review 记录；结论可追溯到 manifest / 报告 / 测试。

## 7. 验收标准

| 验收类型 | 验收方式 | 通过条件 |
|---|---|---|
| 功能验收 | 对照本文 6.1 与 `tasks.md` 各任务验收项 | 全部条目有测试或实测证据 |
| 接口验收 | `docs/sdd/api-spec.yaml` + `tests/test_sdd_api_spec.py` | 契约文件与运行中 OpenAPI 完全一致；每个操作声明统一错误信封 |
| 架构验收 | 对照 `docs/sdd/design.md` 审查模块边界 | 无跨层越权；Evidence First / Retrieval First 约束成立 |
| 性能验收 | 真实压测 + 分位数统计 | 达到 6.2 的 P95 目标（当前未执行） |
| 质量验收 | Phase 12 人工 Gold Set + formal 重跑 | 达到 6.2 的检索/生成目标（当前阻塞于人工确认） |

## 8. 约束与假设

- Python 3.11；FastAPI + Pydantic v2；SQLAlchemy 2.0 + Alembic。
- 主存储 PostgreSQL 17 + pgvector；测试可用 SQLite（明确不支持向量排序）。
- Embedding 默认 bge-m3（1024 维），通过 OpenAI-compatible 接口访问。
- LLM / Reranker 通过 OpenAI-compatible 接口访问，可替换。
- 所有模型调用都可能失败或超时；系统必须显式降级或报错，不能静默伪造结果。

## 9. 风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| 人工 Gold Set 长期未确认 | 无法得出正式质量结论 | 提供审阅辅助工具；状态明确标注 blocked |
| 小样本过拟合 | 参数结论不可外推 | 报告显式标注样本量与局限 |
| 模型服务不可用 | 正式实验无法复现 | 记录 manifest 与失败原因，不伪造结果 |
| SQLite / PG 语义漂移 | 测试通过但生产失败 | 双库测试 + 方言分流类型 + CI PG job |
| 权限/版本过滤漏做 | 数据泄露或错误召回 | SQL 预过滤 + Python 兜底 + 回归测试 |
| 文档与实现漂移 | 新人误判系统能力 | SDD 契约测试 + review + 台账同步 |

## 10. 变更控制

1. 改变范围或成功标准 → 先改本文，再改 `tasks.md`。
2. 改变模块边界、技术选型或数据流 → 先改 `design.md`。
3. 改变接口路径、字段或错误码 → 先改 `api-spec.yaml`（由实现导出），再改代码。
4. 每个任务完成必须回写 `tasks.md` 状态与验证证据。
