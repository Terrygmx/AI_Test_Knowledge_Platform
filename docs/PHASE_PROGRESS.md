# Phase 进度台账

- 最后更新：2026-10-24 18:00（Asia/Shanghai）
- 当前分支：`phase-12-evaluation-dataset`
- 当前 HEAD：`32acdaec9830f87744c0ca7abeb745df6f481134`
- 状态口径：**已完成** / **部分完成** / **未开始**
- 维护规则：每完成一个 Phase、每次 review 收口、每次正式实验重跑后，同步更新本文件；Phase 完成状态以本文件为总览，细节以链接的报告和 review 为准。
- SDD 规范基线：`docs/sdd/proposal.md`、`docs/sdd/design.md`、
  `docs/sdd/api-spec.yaml`、`docs/sdd/tasks.md`；任务状态与验收证据以 `tasks.md` 为准。

> “工程缺口”是代码、测试、正式实验或报告尚未闭环的事项。
> “人工缺口”是必须由人判断、确认或签署的事项，不能用脚本批量伪造。
> “当前结论”只描述已经实际验证的范围，不把小样本或模拟数据升级为生产结论。

## 总览

| Phase | 主题 | 状态 | 已完成工作 | 工程缺口 | 人工缺口 | 依据 |
|---|---|---|---|---|---|---|
| 0 | 项目边界 | 已完成 | `PROJECT_SCOPE.md`、`LEARNING_QUESTIONS.md`、MVP / 非目标 / 成功指标与学习问题 | 后续正式指标口径随实验演进同步更新 | 由项目负责人确认边界与指标是否仍代表真实业务目标 | `docs/PROJECT_SCOPE.md`、`docs/LEARNING_QUESTIONS.md` |
| 1 | FastAPI + PostgreSQL | 已完成 | FastAPI 应用、四实体 CRUD、统一错误信封、请求追踪中间件、Alembic、PostgreSQL 实机验证 | CI 已含 PostgreSQL job（pgvector/pg17 + alembic check + 全量测试），尚未在远程分支实际触发过 | 人工确认 Schema / API 契约是否满足真实业务字段与权限边界 | `README.md`、`docs/handover.md`、review Phase 1 |
| 2 | Embedding + pgvector | 已完成 | Embedding 抽象、1024 维 bge-m3 探针、`knowledge_chunks`、cosine 检索、索引脚本、无向量 Chunk 可观测性 | 大库 HNSW 参数与生产容量尚未验证；当前实验不是 ANN 性能结论 | 人工确认 Embedding 模型、维度和成本是否可作为默认生产选择 | `docs/EMBEDDING_EXPERIMENT.md`、`docs/RAG_EXPERIMENT.md` |
| 3 | 手写最小 RAG | 已完成（实验链路） | Context Assembly、Prompt、LLM 调用、引用编号校验、无证据拒答、超时 / 并发闸门、`scripts/ask.py` | `citation_valid` 仍只表示引用编号合法；正式 Faithfulness / Citation Accuracy 待 Phase 14 收口；同步接口仍是实验性慢接口 | 人工确认拒答文案、可接受的回答延迟和引用展示方式 | `docs/RAG_EXPERIMENT.md`、review Phase 3 |
| 4 | 测试知识模型 | 已完成 | TestCase / Defect / Requirement / Document / KnowledgeChunk 契约、业务编号与 Metadata 收口 | 业务字段未来扩展需补迁移与兼容策略 | 人工确认四类实体的字段语义、编号规则和 Metadata 映射 | `docs/PHASE4_KNOWLEDGE_MODEL.md` |
| 5 | 数据接入 | 已完成 | CSV / Excel / Markdown / PDF 解析与导入、幂等导入、文档 Chunk、导入不自动 Embedding | PDF 仅普通文本；无 OCR / 复杂表格 / 版面还原；导入后需补向量 | 人工抽样确认真实文件解析质量、字段映射和脱敏要求 | `docs/PHASE5_INGESTION.md` |
| 6 | Chunking 实验 | 已完成（小样本） | 固定 256 / 512 / 800 / 1200、Semantic、`entity+heading` 对比；Recall@5 / Recall@10 / MRR、manifest 与报告 | 仅 3 篇模拟文档、40 条 Query；需用约 100 条人工确认数据复验 | 人工确认 Chunk 粒度、Semantic 切分语义及最终默认策略 | `docs/CHUNKING_EXPERIMENT.md`、review Phase 6 |
| 7 | Embedding 实验 | 已完成（小样本） | 固定 `entity+heading`，比较 1024 / 768 / 384 维模型；报告成本、延迟与局限 | 46 个 Chunk 的延迟不能代表 ANN / 生产 P95；需在更大 Gold Set 复验 | 人工确认默认模型、成本上限和是否允许外部模型 | `docs/EMBEDDING_EXPERIMENT.md`、review Phase 7 |
| 8 | BM25 / 词法检索 | 已完成（小样本） | 应用层分词、倒排索引、BM25、ILIKE 对比、Chunk 级可解释分数、1,000 Chunk 护栏 | 每次查询内存建索引，大库需持久化倒排表；仅 24 条 Query / 47 Chunk | 人工确认中文分词、标识符保留规则和生产规模策略 | `docs/LEXICAL_SEARCH_EXPERIMENT.md`、review Phase 8 |
| 9 | Hybrid Search | 已完成（小样本） | Vector + BM25、Weighted / RRF、候选去重、稳定 tie-break、参数扫描、Chunk 级接缝 | 尚未替换统一 `/search` 或 RAG；实验表无 HNSW；小样本参数存在过拟合风险 | 人工确认融合策略、权重、候选深度和何时接入公开 API | `docs/HYBRID_SEARCH_EXPERIMENT.md`、review Phase 9 |
| 10 | Metadata Filter | 已完成（小样本） | 受控过滤 DSL、SQLite / PostgreSQL 跨方言、LIMIT 前过滤、`none / pre / post / during` 实验 | `during` 当前复用 `pre`；未验证 HNSW ANN filtering / iterative scan | 人工确认字段白名单、数组语义和过滤条件的业务优先级 | `docs/METADATA_FILTER.md`、review Phase 10 |
| 11 | Reranker | 部分完成 | Rerank 抽象、OpenAI-compatible `/v1/rerank`、候选深度、同 run `rrf` / `rrf_rerank`、短响应与预检契约 | 缺少正式 `docs/RERANK_EXPERIMENT.md` 和真实 Recall@5 / MRR / 延迟 manifest；默认 reranker 当前不可用；尚未接入统一检索 / RAG | 人工选择可用 reranker、确认模型服务与是否接受其成本 / 延迟 | review Phase 11、`scripts/rerank_experiment.py` |
| 12 | Evaluation Dataset | 部分完成 | 独立 Schema、六类固定配额、100 条 AI 候选、证据 / 过滤条件校验、`candidate` / `formal` 门槛、审阅来源字段 | 当前无数据库 `evaluation_cases` 持久化；候选只覆盖当前小语料 | **100 条 Query 必须逐条人工确认**：意图、过滤条件、Gold 文档 / Chunk、证据片段、答案要点；禁止脚本批量伪造 | `docs/EVALUATION_DATASET.md`、`eval/fixtures/phase12_candidates.json` |
| 13 | Retrieval Evaluation | 部分完成 | 自研 Recall@K / Precision@K / MRR / NDCG、逐 Query Badcase、candidate / formal 模式、稳定结果哈希、报告一致性测试 | 正式 Gold Set 尚未就绪；当前 manifest 是 Phase 8 的 24 条模拟 Query；NDCG 为二值 gain | 人工确认正式 Gold Set 后，决定可接受阈值和是否需要分级相关性 | `docs/RETRIEVAL_EVALUATION.md`、review Phase 13 |
| 14 | Generation Evaluation | 部分完成 | 确定性 Context Recall / Precision、Faithfulness、Answer Relevancy 启发式、Citation Accuracy、拒答一致性、逐样本 Badcase、模拟响应夹具、manifest 与报告；三个 P1 已修复并有回归测试 | formal 仍需完整人工确认数据；尚无真实 LLM 批量回答；Ragas 仅可选占位，参数未固定 | 人工确认 Phase 12 数据；人工定义“可接受 Faithfulness / Citation / 拒答”标准；不能用当前模拟响应作正式质量结论 | `docs/GENERATION_EVALUATION.md`、review Phase 14 |
| 15 | Query Understanding | 部分完成 | Pydantic Schema、确定性意图 / 模块 / 优先级 / 版本 / 关键词解析、LLM JSON 严格校验与重试、失败降级、Phase 12 候选投影评测、manifest 与报告 | 尚无真实 LLM 批量评测；Phase 12 `expected_intent` 不是受控枚举，未计算意图准确率；候选集全部 pending | 人工确认业务意图映射、过滤条件语义和最终 structured output 字段 | `docs/QUERY_UNDERSTANDING_REWRITE.md`、`results/query-understanding-phase12.json` |
| 16 | Query Rewrite | 部分完成 | No-op / deterministic / LLM / composite rewriter、失败降级与延迟记录；Phase 8 候选 Query Rewrite 对比、manifest 与报告 | 当前 deterministic 改写未改变任何 Query；无真实 LLM rewrite 批量实验；正式收益和成本未验证 | 人工确认是否保留改写策略、可接受延迟与模型成本；等待 Phase 12 人工 Gold Set | `docs/QUERY_UNDERSTANDING_REWRITE.md`、`results/query-rewrite-phase16.json` |
| 17 | Citation | 部分完成 | Citation 增加 `chunk_id` / `score`，`RAGResponse.source_lines()` 来源清单契约 | API 未新增独立来源字段；引用正确性仍靠 Phase 14 离线评测 | 人工确认引用展示格式与拒答文案 | `app/schemas/rag.py`、review Phase 17 |
| 18 | 完整 RAG Pipeline | 部分完成 | Understanding → Filter → Hybrid → 可选 Rerank → LLM 全链路 + 逐阶段 trace | 未替换公开 `/rag/answer` 默认路径；trace 未持久化；真实 Rerank 未接入 | 人工确认 trace 存储与在线接入时机 | `app/generation/pipeline.py`、review Phase 18 |
| 19 | Badcase 系统 | 部分完成 | 九阶段分类、闭环状态机、回归 Query 强制绑定；`evaluation_badcases` 表 + Service + `/badcases` API 持久化与闭环推进 | Phase 13/14 badcase 字符串未自动映射 | 人工填写根因与修复假设 | `app/evaluation/badcase.py`、`app/services/badcase_service.py`、review Phase 19 / step2 |
| 20 | 性能统计 | 部分完成 | nearest-rank 分位数、分阶段延迟记录、并发结果契约 | 未跑真实压测；未接 Locust | 人工确认 SLO 与压测方案 | `app/evaluation/performance.py`、review Phase 20 |
| 21 | 增量索引 | 部分完成 | hash 对比生成 CREATE / UPDATE / DELETE 计划；`sync_incremental_index()` 执行器与 `scripts/incremental_index.py`（`--dry-run` / `--only`），复跑幂等 | Chunk 级对比未实现（实体一 Chunk 假设） | 人工确认删除策略与同步频率 | `app/ingestion/incremental.py`、`app/services/incremental_index_service.py`、review Phase 21 / step3 |
| 22 | 版本管理 | 部分完成 | 版本解析、包含式区间、对比请求契约；`version_key` 零填充列 + SQL 区间 pre-filter（迁移 0004） | diff 呈现未定义 | 人工确认未标注版本语义 | `app/retrieval/versioning.py`、`app/retrieval/access_filters.py`、review Phase 22 / step4 |
| 23 | 检索层权限 | 部分完成 | team / project / role 契约与候选过滤；chunk meta `access` 块 SQL pre-filter（PG JSONB / SQLite Python 兜底，fail-closed） | 权限字段仍未进独立数据模型列；规则来源是 metadata 约定 | 人工确认权限模型与角色集 | `app/retrieval/access_control.py`、`app/retrieval/access_filters.py`、review Phase 23 / step4 |
| 24 | Dashboard | 部分完成 | manifest 聚合投影，带 formal / candidate 溯源 | 无 UI 与 HTTP 端点；Token / Top Questions 需日志持久化 | 人工确认展示范围 | `app/evaluation/dashboard.py`、review Phase 24 |
| 25 | Agentic RAG | 部分完成 | 工具白名单、Planner 协议、max_steps 受控循环；六个工具绑定真实 Hybrid / 实体检索（`build_default_registry`），证据带来源标识 | LLM Planner 未实现 | 人工确认工具清单与对比语义 | `app/agent/tools.py`、`app/agent/retrieval_tools.py`、review Phase 25 / step7 |
| 26 | 回归测试 Agent | 部分完成 | 四类证据 → 确定性计划草稿 → Terry 人工批准硬门槛 | 未绑定检索工具；用例执行器在项目边界外 | 人工批准每次执行 | `app/agent/regression_planner.py`、review Phase 26 |
| 27 | 实验矩阵 | 部分完成 | 跨 manifest 聚合 + 缺失行显式标记 | Vector / Rerank / final 行待正式数据 | 人工评定 final 行 | `app/evaluation/experiment_matrix.py`、review Phase 27 |
| 28 | 项目文档 | 部分完成 | `docs/ARCHITECTURE.md` 总览 + 既有文档索引 | BADCASE / PERFORMANCE 独立成篇待数据 | 人工审阅文档准确性 | `docs/ARCHITECTURE.md`、review Phase 28 |
| 29 | 8 周计划 | 已覆盖 | 计划中的 Week 1–8 内容已映射到 Phase 0–28 实际进度 | 无 | 无 | `docs/PHASE_PROGRESS.md` |
| 30 | 最终验收 | 阻塞于人工门槛 | 工程侧组件已实现（见各 Phase review） | 正式指标 / Gold Set / SLO 验收未完成 | **Phase 12 的 100 条人工确认是总闸门** | `docs/PHASE_PROGRESS.md` |

## Phase 0–28 已完成工作明细

### 基础设施与知识建模

- **Phase 0**：完成项目范围、用户、数据、用例、MVP、非目标、成功指标与 10 个 RAG 学习问题。
- **Phase 1**：完成 FastAPI 路由、Pydantic Schema、依赖注入、统一异常处理、`X-Request-ID` / `X-Process-Time-Ms`、四实体 CRUD、Alembic 与 PostgreSQL 实机验证。
- **Phase 2**：完成 Embedding 抽象与 bge-m3 探针、`knowledge_chunks`、pgvector cosine 检索、实体索引脚本、Chunk 无向量可观测性。
- **Phase 3**：完成最小 RAG 链路：Vector Search → Context Assembly → Prompt → LLM → Answer + Citation；支持无证据拒答、引用编号校验、可选距离阈值、超时与并发保护。
- **Phase 4**：完成 TestCase / Defect / Requirement / Document 与 KnowledgeChunk 的字段、编号、Metadata 和 API 契约收口。
- **Phase 5**：完成 CSV / Excel / Markdown / PDF 接入、幂等导入、文档 Chunk、导入与 Embedding 解耦。

### 检索实验与接缝

- **Phase 6**：完成六种 Chunking 策略对比、策略无关的证据级 Gold Set、Recall@5 / Recall@10 / MRR 与可复现 manifest。
- **Phase 7**：完成三个 Embedding 模型的隔离实验，固定 Chunking 变量，记录 Recall / MRR / 延迟 / 模型维度与实验边界。
- **Phase 8**：完成中文友好的 BM25、ILIKE 基线对比、Chunk 级可解释检索接口、标识符 / 版本号 / 组合关键词失败模式实验。
- **Phase 9**：完成 Vector + BM25 的 Weighted / RRF 融合、候选去重、稳定排序、参数扫描与 Chunk 级 `hybrid_search()` 接缝。
- **Phase 10**：完成受控 Metadata Filter DSL、跨 SQLite / PostgreSQL 的预过滤、候选饥饿实验与 Chunk 检索接缝。
- **Phase 11**：完成 Rerank 抽象、候选深度、同 run baseline / rerank 双通道、provider 短响应契约与预检；正式实验尚未闭环。

### 评测体系

- **Phase 12**：完成 100 条六类候选、审阅状态与来源契约、文档 / Chunk 双层证据校验、过滤条件校验、`candidate` / `formal` 门槛。
- **Phase 13**：完成自研检索指标、逐 Query Badcase、candidate / formal 模式、稳定结果哈希、报告与 manifest 一致性测试。
- **Phase 14**：完成确定性生成指标、引用编号与来源校验、拒答一致性、检索 / 生成 Badcase 归因、模拟响应夹具与可复现 manifest；三个 P1 指标问题已修复，当前仍是工具链与候选回归结果。
- **Phase 15**：完成 Query Understanding Schema、确定性规则基线、LLM JSON 严格校验 / 重试 / 降级，以及 Phase 12 候选投影报告；尚未完成正式意图准确性验收。
- **Phase 16**：完成 Query Rewrite 抽象、No-op / deterministic / LLM / composite 策略与 Phase 8 候选对比；当前没有改写收益或真实 LLM 成本结论。
- **Phase 17**：完成 Citation `chunk_id` / `score` 与 `source_lines()` 来源清单契约。
- **Phase 18**：完成完整 RAG Pipeline 与逐阶段 trace；未替换公开 API 默认路径。
- **Phase 19**：完成 Badcase 分类与闭环状态机；未持久化。
- **Phase 20**：完成分阶段延迟统计契约；未跑真实压测。
- **Phase 21**：完成增量索引计划器；执行器未接入。
- **Phase 22**：完成版本解析与区间契约；SQL 过滤未接入。
- **Phase 23**：完成检索层权限契约；数据模型未建模权限字段。
- **Phase 24**：完成 Dashboard manifest 聚合；无 UI。
- **Phase 25**：完成 Agent 工具白名单与受控循环；工具未绑真实检索。
- **Phase 26**：完成回归计划草稿与 Terry 人工批准门槛；执行器在边界外。
- **Phase 27**：完成实验矩阵聚合与缺失行标记。
- **Phase 28**：完成架构总览文档；其余文档复用既有报告。

## 当前仍差什么

### 工程缺口

| 优先级 | 缺口 | 影响 | 完成条件 |
|---|---|---|---|
| P1 | Phase 11 正式 Rerank 实验未完成 | 无法判断 Rerank 是否真正提升 Recall / MRR | 可用 reranker + PostgreSQL，生成正式报告和 manifest |
| P1 | Phase 13 正式检索评测未运行 | 当前只有 24 条模拟 Query 的 candidate 结果 | Phase 12 人工确认后以 formal 模式重跑并归档 |
| P1 | Phase 15 / 16 尚无正式数据与真实 LLM 结论 | 只能验证 Schema、降级路径和候选工具链 | Phase 12 人工确认；固定模型、Prompt、重试与延迟预算后重跑 |
| P2 | Phase 15 未建立业务 intent 到受控枚举的人工映射 | 不能计算有意义的意图准确率 | 人工确认 intent 词表与映射后再评测 |
| P2 | Phase 10 未验证 HNSW ANN filtering / iterative scan | 不能声称近似索引过滤质量 | 建 HNSW 隔离表并做召回 / 延迟实验 |
| P2 | Phase 6–10 均为小样本实验 | 结论不能外推到真实测试知识库 | 用约 100 条人工 Gold Set 复验 |

### 人工缺口

| 优先级 | 人工事项 | 为什么不能自动完成 | 完成标准 |
|---|---|---|---|
| P0 | 逐条审阅 Phase 12 的 100 条 Query | 意图、过滤条件、Gold 文档 / Chunk、证据和答案要点都是业务判断；AI 只能生成候选 | 每条填写 `review_status=confirmed/rejected`、真实 `reviewed_by`、带时区 `reviewed_at` 和必要备注 |
| P0 | 确认 Phase 14 的验收口径 | 需要业务定义什么叫“忠实”“可引用”“应拒答”，不能只看一个分数 | 明确 Faithfulness、Citation Accuracy、Refusal Correct 的阈值与人工复核流程 |
| P1 | 确认 Phase 11 Reranker 选型与成本 | 涉及模型服务可用性、延迟、费用和业务收益 | 确认可用模型、候选深度、延迟预算和正式实验方案 |
| P1 | 确认 Chunking / Embedding / Hybrid / Filter 的业务默认值 | 小样本指标不足以单独决定生产配置 | 结合正式 Gold Set、成本和人工 Badcase 复核做决定 |
| P1 | 抽样复核真实数据接入结果 | 解析器能读文件不等于字段、章节和业务语义正确 | 对 CSV / Excel / Markdown / PDF 各抽样验收，记录脱敏与失败样例 |
| P2 | 定义生产 SLO 与安全边界 | 超时、并发、权限、日志和敏感数据策略需要负责人决策 | 明确 API P95、最大并发、日志脱敏、权限模型和告警规则 |
| P2 | 确认后续 Phase 17–30 的优先级 | 计划顺序不等于当前业务优先级 | 每个阶段开始前确认范围、验收标准和是否继续 |

## 当前可安全宣称的结论

- Phase 0–10 的核心链路和小样本实验已经完成；它们的指标只适用于当前模拟语料和实验条件，不是生产容量或真实业务分布结论。
- Phase 11 只有接缝与实验逻辑，正式 Rerank 报告尚未完成。
- Phase 12 只有 AI 候选，**不是人工确认的 Gold Set**。
- Phase 13 的 candidate 结果只验证指标和 Badcase 归因，不是正式检索质量结论。
- Phase 14 的三个 P1 已修复，但模拟响应只验证工具链；在人工确认数据就绪前，不能宣称真实 Faithfulness、Answer Relevancy 或 Citation Accuracy 已验收。
- Phase 15 / 16 的 candidate 结果只验证 Schema、规则、降级路径与 manifest；当前没有正式意图准确率、Rewrite 收益或真实 LLM 成本结论。

## 下一步顺序

1. 人工逐条确认 Phase 12 的 100 条 Query，尤其是 intent、过滤条件与答案要点。
2. 用正式 Gold Set 重跑 Phase 13 / Phase 14 / Phase 15 / Phase 16。
3. 准备可用 reranker，完成 Phase 11 正式实验。
4. 固定真实 LLM 的 Prompt、重试和延迟预算，再决定是否保留 Query Rewrite。
5. Phase 17–28 组件已完成契约层实现；逐项接入正式数据的顺序
   以 Phase 30 验收清单为准。

## 相关记录

- 总体计划：`docs/AI_Test_Knowledge_Platform_Implementation_Plan.md`
- 交接说明：`docs/handover.md`
- Phase 11 review：`review/review-phase-11-rerank-20261020-1840.md`
- Phase 12 review：`review/review-phase-12-evaluation-dataset-20261020-1932.md`
- Phase 13 review：`review/review-phase-13-retrieval-evaluation-20261022-2120.md`
- Phase 14 review：`review/review-phase-14-generation-evaluation-20261022-2227.md`
- Phase 15 / 16 报告：`docs/QUERY_UNDERSTANDING_REWRITE.md`
