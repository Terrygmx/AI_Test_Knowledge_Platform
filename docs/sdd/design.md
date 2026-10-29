# Design：AI Test Knowledge Platform 系统设计

- 文档状态：生效（v1.0，2026-10-29）
- 关联文档：`docs/sdd/proposal.md`、`docs/sdd/api-spec.yaml`、`docs/ARCHITECTURE.md`
- 本文是实现约束的单一来源；实现与本文冲突时，以本文为准并修正实现。

## 1. 架构总览

```text
HTTP (FastAPI)
  └─ app/api/               路由、依赖注入、统一错误信封、中间件
       └─ app/services/     业务编排（CRUD / 检索 / Badcase / 增量索引）
            ├─ app/ingestion/    解析 → 切分 → Chunk
            ├─ app/retrieval/    Embedding / Vector / BM25 / Hybrid / Filter
            ├─ app/rerank/       Rerank 接缝
            ├─ app/query/        Query Understanding / Rewrite
            ├─ app/generation/   Context / Prompt / LLM / Citation / Pipeline
            ├─ app/evaluation/   数据集契约 / 指标 / Badcase / 实验矩阵
            └─ app/agent/        工具白名单 / Planner / 回归计划
                 └─ app/models/  ORM 实体（Schema 单一事实来源）
                      └─ PostgreSQL 17 + pgvector（Alembic 管理）
```

分层规则：

- **路由层不做业务判断**：只做参数校验、调用 service、返回 Schema。
- **service 层不拼 HTTP 响应**：业务错误统一抛 `DomainError` 子类。
- **评测模块不依赖 HTTP**：直接调用 service / 纯函数，保证离线可复现。
- **模型层不写业务分支**：方言差异通过 `app/models/types.py` 的类型装饰器处理。

## 2. 模块边界

| 模块 | 职责 | 明确不做 |
|---|---|---|
| `app/core` | 配置、DB 会话、日志、请求上下文、领域异常 | 业务规则 |
| `app/api` | 路由、错误信封、请求追踪中间件 | 检索/生成算法 |
| `app/models` | ORM 实体、可移植列类型 | 业务编排 |
| `app/schemas` | Pydantic 请求/响应契约 | 持久化 |
| `app/services` | CRUD、统一检索、Badcase、增量执行 | HTTP 细节 |
| `app/ingestion` | CSV/Excel/Markdown/PDF 解析、Chunk 生成、增量计划 | 向量生成 |
| `app/retrieval` | Embedding、向量/词法检索、融合、过滤、版本/权限 | 生成答案 |
| `app/rerank` | 候选精排、provider 契约 | 重新召回 |
| `app/query` | 意图/过滤条件解析、查询改写 | 直接检索或生成 |
| `app/generation` | 上下文组装、Prompt、LLM 调用、引用校验、Pipeline | 决定 Gold 数据 |
| `app/evaluation` | 数据集契约、指标、Badcase、报告 | 伪造人工确认 |
| `app/agent` | 工具白名单、Planner、回归计划 | 绕过工具生成证据 |

## 3. 数据模型与存储

### 3.1 表

| 表 | 说明 | 关键约束 |
|---|---|---|
| `test_cases` | 测试用例事实来源 | `case_id` 唯一 |
| `defects` | 缺陷事实来源 | `bug_id` 唯一 |
| `requirements` | 需求事实来源 | `req_id` 唯一 |
| `documents` | 文档元数据 | `doc_id` 唯一 |
| `knowledge_chunks` | 检索表示（可重建） | `(source_type, source_id, chunk_index)` 唯一 |
| `evaluation_badcases` | Badcase 闭环 | `badcase_id` 唯一 |

### 3.2 迁移

| Revision | 内容 |
|---|---|
| `0001` | 四实体基础表 + 扩展（vector / pg_trgm） |
| `0002` | `knowledge_chunks` + HNSW cosine 索引 |
| `0003` | `evaluation_badcases` |
| `0004` | `knowledge_chunks.version_key`（版本区间 SQL 排序键） |

规则：ORM 是 Schema 单一事实来源，迁移是其版本化投影；`tests/test_migrations.py`
静态解析迁移并与 ORM 对比，`alembic check` 在 CI 中执行。

### 3.3 关键字段

- `knowledge_chunks.content_hash`：内容 SHA-256，增量索引判断依据。
- `knowledge_chunks.version_key`：零填充版本键（`v2.10` → `0002.0010.0000`），
  让 SQL 字符串比较等价数值比较；非法/缺失版本为 NULL，不匹配任何区间。
- `knowledge_chunks.metadata.access`：权限块
  `{"teams": [...], "projects": [...], "min_role": "editor"}`；缺失表示公开。

## 4. 检索与生成链路

```text
question
 → QueryUnderstanding（intent / module / priority / version / filters / semantic_query）
 → QueryRewrite（noop / deterministic / llm / composite，失败降级）
 → Embedding（1024 维）
 → Vector TopK ∥ BM25 TopK
 → Metadata Filter + VersionRange + AccessFilter（均在 SQL WHERE / LIMIT 前）
 → Fusion（weighted / rrf）
 → Rerank（可选，candidate → top_n）
 → Context Assembly（编号、来源、分数）
 → Prompt → LLM
 → Citation 校验（编号越界整体拒绝）
 → RAGResponse + PipelineTrace
```

### 4.1 检索不变量

1. 过滤必须在 LIMIT 之前（避免候选饥饿）。
2. 缺失字段、`null`、错误类型不匹配任何过滤条件。
3. 未知过滤字段直接报错，不静默忽略。
4. 两路候选按 `(source_type, source_id, chunk_index)` 去重，tie-break 稳定。
5. SQLite 不支持向量排序时明确报错，不伪装结果。
6. 版本区间在 `version_key` 上做字符串范围比较；无版本不匹配。
7. 权限每个维度独立判断：缺省不限，畸形 fail-closed。

### 4.2 生成不变量

1. Prompt 只包含本次检索到的证据，并带编号。
2. 回答引用编号必须落在 `1..len(citations)`；越界整体拒绝。
3. 无证据或证据不足时返回明确拒答文案。
4. `citation_valid` 只表示引用编号合法，不表示 Faithfulness。
5. 同步 LLM 接口必须有超时（503）与并发闸门（429）。

## 5. 评测体系设计

### 5.1 数据集契约

- Phase 12 数据集为独立 Schema（`app/evaluation/phase12_dataset.py`），
  不复用 Phase 6 的 `ChunkingDataset`。
- 六类固定配额：testcase_search 30 / defect_search 20 / document_qa 20 /
  cross_version 10 / multi_condition 10 / unanswerable 10（合计 100）。
- 每条包含 query、expected_intent、expected_filters、gold_documents、
  gold_chunks、expected_answer_points、answerable、review 元数据。
- 证据必须真实出现在语料中；Gold 文档与 Gold Chunk 必须一致；
  过滤条件不得过滤掉 Gold。

### 5.2 审阅状态机

```text
pending ──人工确认──> confirmed
   └──────人工驳回──> rejected
```

- `confirmed` / `rejected` 必须带 `reviewed_by=Terry`、带时区 `reviewed_at`、
  非空 `review_notes`。
- `pending` 禁止携带任何审阅元数据。
- `formal` 模式要求全部 Query 为 `confirmed`，否则直接失败。

### 5.3 指标

| 层 | 指标 |
|---|---|
| 检索 | Recall@K、Precision@K、MRR、NDCG（二值 gain） |
| 生成 | Faithfulness、Citation Accuracy、Refusal Correct、Answer Relevancy（启发式）、Context Recall/Precision |
| 归因 | retrieval_error / generation_error_*（缺要点、错误引用、无依据断言等） |
| 工程 | 分阶段延迟、P50/P95/P99、Badcase 闭环状态 |

所有报告写入 `results/*.json`，必须包含模式（candidate / formal）、数据集哈希、
参数与逐样本明细。

## 6. 关键决策（ADR）

| ID | 决策 | 理由 | 代价 |
|---|---|---|---|
| ADR-1 | PostgreSQL + pgvector 单库 | 关系、Metadata、全文、向量一致；少一套存储 | 需要 PG 专属测试 |
| ADR-2 | 手写 Pipeline | 学习与可解释性；每个阶段可评测 | 开发量大于框架封装 |
| ADR-3 | Evidence First | 引用必须可追溯，拒答优于幻觉 | 召回不足时体验偏保守 |
| ADR-4 | candidate / formal 双模式 | 防止 AI 候选冒充人工 Gold Set | 正式结论依赖人工 |
| ADR-5 | RRF 为默认融合 | 不看原始分值尺度，鲁棒 | 丢失分数强度信息 |
| ADR-6 | 过滤下沉 SQL | 避免候选饥饿与信息泄露 | 方言适配成本 |
| ADR-7 | `version_key` 物化排序键 | SQL 无法直接数值比较版本字符串 | 写入时需维护派生列 |
| ADR-8 | 权限 fail-closed | 权限错误宁可漏召回不泄露 | 配置错误时可见性下降 |
| ADR-9 | 工具白名单 + 受限步数 | Agent 自由度可控、证据来源单一 | 灵活性受限 |
| ADR-10 | 确定性优先，LLM 可替换 | 可复现、可降级、成本可控 | 语义覆盖有限 |

## 7. 非功能约束

### 7.1 测试

- 默认 SQLite 内存库；`TEST_DATABASE_URL` 指向 PG 时跑真实方言测试。
- 向量检索、HNSW、pg_trgm 等 PG 能力必须有 PG 实测。
- 契约测试：`tests/test_sdd_api_spec.py` 校验 `api-spec.yaml` 与运行中 OpenAPI 一致。
- 迁移测试：静态解析 + SQLite upgrade/downgrade + PG `alembic check`。

### 7.2 CI

- Job 1：ruff + SQLite 全量测试。
- Job 2：pgvector/pg17 service + `alembic upgrade head` + `alembic check` + PG 全量测试。
- 模型服务不可用不得导致 CI 失败；相关测试按既有 skip 逻辑处理。

### 7.3 安全

- 错误信封不回显用户输入；500 只返回通用文案。
- 日志不记录密钥；`.env` 不入库。
- 权限过滤在检索层；畸形 access fail-closed。
- Agent 工具只读，写操作不在 MVP 工具白名单内。

### 7.4 可观测性

- `X-Request-ID` / `X-Process-Time-Ms` 中间件。
- PipelineTrace 记录每阶段候选身份、分数与耗时。
- 评测 manifest 记录数据集哈希、参数、模式与逐样本结果。

## 8. 已知缺口

| 缺口 | 影响 | 解除条件 |
|---|---|---|
| Phase 12 100 条 Query 未人工确认 | 无法进行 formal 验收 | Terry 逐条审阅并回填 |
| Reranker 默认模型不可用 | 正式 rerank 实验无法跑 | 选定可用模型与服务 |
| 未做真实压测 | 无 P95 / SLO 结论 | 定义 SLO + 压测方案 |
| Ragas 仅可选占位 | 生成指标口径未与业界对齐 | 固定版本、参数与样本 |
| 权限字段存于 metadata | 无法独立索引/管理 | 权限模型确认后建列 |
| LLM Planner 未实现 | Agent 仅确定性路由 | 需要严格 JSON 校验与预算 |

## 9. 文档与契约关系

| 文档 | 作用 | 更新触发 |
|---|---|---|
| `proposal.md` | 为什么做、范围、成功标准 | 范围/目标变化 |
| `design.md` | 结构、边界、ADR、非功能约束 | 设计/选型变化 |
| `api-spec.yaml` | 机器可读接口契约 | 路由/字段/错误变化 |
| `tasks.md` | 依赖排序的任务与验收证据 | 任务状态变化 |
| `docs/PHASE_PROGRESS.md` | 阶段完成度与人工缺口 | 每阶段收口 |
| `review/*.md` | 独立评审记录 | 每次评审 |
