# Handover：AI Test Knowledge Platform 项目交接文档

> 写给接手的 agent / 开发者。读完本文 + `README.md` + `CHANGELOG.md` 即可上手干活。
> 更完整的背景：`docs/AI_Test_Knowledge_Platform_V2.md`（总体方案）、
> `docs/AI_Test_Knowledge_Platform_Implementation_Plan.md`（Phase 0–30 开发计划）。
> Phase 完成度、工程缺口和必须人工完成的事项以
> `docs/PHASE_PROGRESS.md` 为统一总览；每次 phase / review 收口后同步维护。

---

## 1. 这是个什么项目

面向**测试工程**的知识智能平台：统一接入测试用例、缺陷、需求/产品文档与测试规范，
通过结构化知识建模 + Hybrid Retrieval（Vector + Keyword + Metadata Filter）+ Rerank + RAG，
为测试人员以及后续的**智能回归测试 Agent** 提供**可追溯**的知识检索与问答能力。

它不是"知识库聊天机器人"，而是知识基础设施。项目同时是一个学习驱动型项目：
方案文档里每个 Phase 都标了「个人 vs AI」的分工比例，架构判断主要由人类完成。

### 核心原则（所有设计决策都由此出发）

| 原则 | 含义 |
|---|---|
| **Retrieval First** | 先证明检索正确，再做生成 |
| **Evidence First** | 回答必须有来源，不允许凭空生成 Case ID / Bug ID；**没有打分依据就不返回 score** |
| **结构化优先** | TestCase / Defect / Requirement 不能当普通文本 Chunk 处理；原始实体 ≠ 检索单元 |
| **Evaluation First-class** | 改 Chunk / Embedding / TopK / Rerank 都必须能重新评测对比 |
| **LLM 可替换** | Embedding / Rerank / LLM 全部走 OpenAI-compatible 接口，配置切换 |

---

## 2. 技术栈与环境

- **Python 3.11 + uv**（`uv sync` 建虚拟环境；`uv` 可能不在 PATH，用 `~/.local/bin/uv`）
- **FastAPI + Pydantic v2 + pydantic-settings**
- **PostgreSQL 17 + pgvector 0.8**（关系数据 / Metadata / 全文检索 / 向量四合一，**不引入 Milvus**）
- **SQLAlchemy 2.0 + Alembic**（Schema 单一事实来源，见 §4）
- **Embedding**: bge-m3（1024 维，本机 OMLX OpenAI-compatible API）
- **Reranker**: OpenAI-compatible `/v1/rerank`；本地模板使用
  `Qwen3.5-9B-DFlash`，运行前必须通过预检。**LLM**: 本机 OMLX
  `Ling-3.0-tiny-oQ6e`（默认）↔ Ollama / 云 API
- **Phase 11 当前状态**：Rerank 候选深度、同 run 双通道、短响应契约与
  预检已实现并通过 SQLite 测试；正式报告 / manifest 尚待 PostgreSQL 与
  reranker 服务可用后生成。
- 工程：pytest + ruff（lint 规则见 `pyproject.toml`）

### 环境变量

统一从 `.env` 读取，模板 `.env.example`。关键点：

- Embedding 默认走本机 OMLX（`http://127.0.0.1:28080/v1`，模型
  `bge-m3-mlx-4bit`，Key 为 `omlx`）；`RERANK_API_KEY` 为空时 Rerank 不可运行。
  `GET /health` 的 `capabilities` 会如实显示各项能力可用性
- LLM 默认与本机 Embedding 共用 OMLX（`http://127.0.0.1:28080/v1`，模型
  `Ling-3.0-tiny-oQ6e`，Key `omlx`）；Ollama 仅作为可切换通道保留
- 数据库连接只有 `.env` 一个来源（`alembic.ini` 不写死 url，由 `alembic/env.py` 从 config 读）

---

## 3. 目录结构

```
app/
├── core/        # config / db（engine+session）/ logging / exceptions（领域异常）/ context
├── api/         # 路由层 + middleware（X-Request-ID）+ errors（统一错误信封）
├── models/      # ORM 实体 = Schema 唯一事实来源；types.py 跨方言列类型；enums.py 受控词表
├── schemas/     # Pydantic 请求/响应模型（入参规范化在这里做）
├── services/    # EntityService CRUD 基类 + 四实体服务 + UnifiedSearchService
├── ingestion/   # Phase 5：表格 / Markdown / PDF 解析与文档导入
├── retrieval/   # Embedding 抽象、Chunking、分词 / BM25、Weighted / RRF 融合
├── rerank/      # Phase 11 Rerank 接缝（正式报告待生成）
├── generation/  # Phase 3 最小 RAG；Phase 17 扩展完整 Citation Pipeline
└── evaluation/  # 数据集契约（含 Phase 12）、Recall@K / MRR 与各阶段实验
alembic/         # 迁移脚本（0001 初始表 / 0002 knowledge_chunks）
tests/           # 通过用例以当前实测为准（SQLite 默认路径）
eval/ data/      # 评测数据集（含 Phase 12 候选）/ 原始语料
scripts/db_init.sh  # 一键：docker compose up → 等 healthy → alembic upgrade head
```

---

## 4. 关键架构决策（接手者必读，不要轻易推翻）

1. **Schema 由 ORM + Alembic 单一管理**。
   `app/models/` 是唯一事实来源，`alembic/versions/` 是它的版本化投影。
   早期的 `sql/init/*.sql` 手工 DDL 已废除（双轨维护必然漂移）。
   改表流程：改 `app/models/` → `uv run alembic revision --autogenerate -m "..."` → `upgrade head`。
   `tests/test_migrations.py` 盯住「ORM 与迁移一致」和「迁移可执行」。

2. **跨方言可移植列类型集中在 `app/models/types.py`**。
   测试默认跑 SQLite 内存库（无 Docker 也能 TDD），PG 的 `JSONB`/`TEXT[]`/`BIGSERIAL`
   在这里按方言分流。新列类型统一加在这里，业务代码禁止写 `if dialect == "postgresql"`。
   注意坑：SQLite 主键必须 `BigInteger().with_variant(Integer, "sqlite")`（`pk_type()`）。

3. **枚举列用 TEXT + StrEnum**，不用 PG 原生 enum（`ALTER TYPE` 迁移成本高）。
   `app/models/enums.py` 全部用 `StrEnum`（`str(member)` 即规范值），
   Pydantic 负责大小写归一化与空白清洗。

4. **统一错误信封**：所有错误都是 `{"error": {code, message, request_id, detail}}`；
   业务错误抛 `DomainError` 子类（`NotFoundError`→404，`DuplicateError`→409），
   路由函数不自己拼 error dict。`extra="forbid"` 的更新入参把拼错字段名拦在 422。

5. **公开 API 用业务编号寻址**（Case ID / Bug ID），业务编号不可修改（PATCH 传了会 422），
   因为它们是将来 Citation 的锚点。

6. **统一检索 API 是 Hybrid Retrieval 的接缝**：`GET /api/v1/search` 的
   `SearchResponse` 契约保持稳定，Phase 3 起内部换成 Vector+Keyword+Fusion+Rerank，
   调用方与评测代码不用改。当前不返回 `score`（Evidence First，没打分依据不编数字），
   每条 hit 带 `matched_on` 说明"为什么被召回"。

7. **业务实体与检索单元分离**：`test_cases` 等四张表是原始事实来源；
   `knowledge_chunks`（Phase 2 引入）才是检索表示。

8. **弱关联不建外键**：`defects.related_cases` 是用例编号数组，不设 FK——
   检索场景"能找到"比"引用完整"更重要。

---

## 5. 当前进度

**分支 `phase-12-evaluation-dataset`，Phase 0–14 核心链路已实现；Phase 12
候选数据集已完成，等待人工确认；Phase 14 当前为模拟响应候选回归。数据库实机验证已完成。**

### 已完成

- Phase 0 完成：`PROJECT_SCOPE.md`（边界 / 非目标 / 指标）与 `LEARNING_QUESTIONS.md`
- Phase 0 之前的全部基础：目录骨架、依赖（uv + Python 3.11）、配置层、数据访问层、日志
- **Phase 1（FastAPI + PostgreSQL）全部完成，全程 TDD（先红后绿）**
  （测试数量以当前实际输出为准）
  - ORM 四实体表（`test_cases` / `defects` / `requirements` / `documents`）+ 索引
  - Pydantic Schema（受控词表 + 入参规范化 + `to_orm_dict(mode="json")`）
  - 服务层：`EntityService` 基类（CRUD + 等值过滤 + 关键词 ILIKE + 分页）、四实体服务、
    `UnifiedSearchService` 跨实体统一检索
  - API：`GET /health`（含 DB 与 AI 能力自检）、四实体完整 CRUD、`GET /api/v1/search`
  - 统一错误信封 + `X-Request-ID` / `X-Process-Time-Ms` 中间件
  - Alembic 初始迁移 `0001`（含 `CREATE EXTENSION vector / pg_trgm`，仅 PG 分支执行）
- **PostgreSQL 17.11 实机验证通过**：Docker Compose 启动 `pgvector/pgvector:pg17`，
  Alembic 升级到 `0002 (head)`；`vector 0.8.7` / `pg_trgm 1.6` 已安装；
  历史记录：专用测试库 `aitkp_test` 上 314 passed + 1 skipped；
  `GET /health` 返回数据库检查 `true`（当前测试数量以实跑为准）
- Phase 2 核心链路：`knowledge_chunks` ORM / Schema / 迁移、Embedding 抽象、
  实体到 Chunk 的索引脚本、PostgreSQL cosine 向量检索、Chunk API
- Phase 2 验证边界：Fake Embedding 只证明链路可运行；本机 OMLX 已具备真实
  bge-m3 能力，相似度 / 索引 / pgvector 链路已写入 `docs/EMBEDDING_EXPERIMENT.md`；
  Recall@K / MRR 仍需人工 Gold Set 验证
- Phase 3 真实链路已完成：`POST /api/v1/rag/answer`、`scripts/ask.py`、
  Context Assembly（字符预算）、Citation 编号校验、可选 `max_distance`、无证据拒答；
  PostgreSQL + pgvector + OMLX Embedding + OMLX LLM 已端到端验证
- 真实链路结果记录在 `docs/RAG_EXPERIMENT.md`：正常回答、引用追溯、无关问题拒答
  与严格 `max_distance` 过滤均通过；`Ling-3.0-tiny-oQ6e` 的推理输出会使单次回答
  延迟达到约 11–60 秒；API 已加 `RAG_REQUEST_TIMEOUT`（默认 45 秒）与
  `RAG_MAX_CONCURRENCY` 并发闸门，并标注为实验性慢接口。
  **超时（503 `llm_error`）只是提前放弃等待**：底层 LLM 调用无法被中断，
  后台守护线程会继续跑到 `LLM_TIMEOUT`（默认 30 秒，配置层强制
  `LLM_TIMEOUT <= RAG_REQUEST_TIMEOUT`）；后台任务用自带的会话，
  不碰请求级 Session。**满载返回 429 `rag_overloaded`**（带 `Retry-After: 1`），
  与超时语义分开；生产使用前仍需做异步化与性能评估
- Phase 3 的 `citation_valid` 只表示引用编号合法；Faithfulness / Citation Accuracy
  仍需 Phase 14 的真实评测，不能据此宣称回答有据
- Phase 4 已完成知识模型收口：TestCase / Defect 字段契约、业务编号校验、
  KnowledgeChunk 边界与完整 Metadata 映射；详见 `docs/PHASE4_KNOWLEDGE_MODEL.md`
- Phase 5 已完成数据接入：CSV / Excel 幂等导入、Markdown 标题树与文档 Chunk、
  普通文本 PDF；详见 `docs/PHASE5_INGESTION.md`
- Phase 6 已完成 Chunking 对比实验：固定 256/512/800/1200、Semantic 与
  `entity+heading` 六种策略；Gold Set 按来源和原文证据标注，数据集 SHA-256、
  `git_commit` / `git_dirty`、脱敏命令、实际策略配置与分段耗时均写入 manifest；
  详见 `docs/CHUNKING_EXPERIMENT.md`
- Semantic 直接比较相邻句子，不再使用重叠窗口；低于 0.55 才切分，短于 40 字的
  Group 在同一章节内合并，章节边界优先于最短长度约束。文档 Chunk 的
  `span_start` / `span_end` 指向非重叠正文，overlap 与 heading path 分列 metadata
- Phase 6 实测结论只覆盖 3 篇模拟文档、40 条 Query：`entity+heading` 当前 MRR
  最高（0.936），Semantic Recall@5 最高（0.975）但切分阶段额外调用 Embedding，
  总准备成本更高；不可回答 Query 仍全部在 Top-10 有结果，不能把 Top-K 非空
  当作证据充分
- Phase 7 已完成 Embedding 模型对比：固定 Phase 6 的 `entity+heading` 基线，
  在隔离库 `aitkp_embedding` 中比较 1024 / 768 / 384 维三个 OMLX 模型；
  `bge-m3-mlx-4bit` Recall@5 0.966 / MRR 0.936，`embeddinggemma-2-oQ8e`
  0.941 / 0.934，`all-MiniLM-L6-v2` 0.466 / 0.447。默认继续使用 bge-m3；
  详见 `docs/EMBEDDING_EXPERIMENT.md`，原始 manifest 为
  `results/embedding-phase7.json`
- Phase 7 每个模型使用独立向量表，未截断 / 补零；实验表未建 HNSW，因此只比较
  语义质量与调用成本，不把 46 个 Chunk 上的精确扫描延迟当作 ANN 结论
- Phase 8 已完成 BM25 / Lexical Search：应用层分词 + 倒排索引，ASCII 标识符 /
  版本号整串保留，中文 unigram + bigram（Unicode 字母数字标识符也整串保留）；
  `KnowledgeChunkService.lexical_search()`
  在 SQLite / PostgreSQL 上返回 `(chunk, bm25_score, matched_terms)`。独立
  Gold Set 上 `ILIKE` Recall@5 0.056、BM25 0.944，MRR 0.056 / 0.792；
  `P0 BUG-1234 login-api v1.3` 在 `ILIKE` 下 0 命中、BM25 首位命中 `BUG-1234`。
  详见 `docs/LEXICAL_SEARCH_EXPERIMENT.md`，manifest 为
  `results/lexical-phase8.json`
- Phase 8 数据集不是 Phase 6 的完全复用：复用 12 TestCase / 5 Defect /
  5 Requirement / 3 Document，并新增 `BUG-1234`，最终 6 Defect、47 Chunk；
  该 Defect 是组合关键词坏例的来源。manifest 要求从干净提交生成，当前实验的
  `git_commit` / `git_dirty` 以文件内记录为准
- 当前统一 `/search` 仍走 `ILIKE` 且不返回 score；Phase 8 的 BM25 是独立 Chunk
  检索接缝，供 Phase 9 融合使用。不要在没有融合 / 归一化契约时把 BM25 score 直接
  塞进现有 `SearchHit`
- Phase 9 已完成 Hybrid Search 实验：固定 Phase 8 的 24 条 Query / 47 个 Chunk，
  在隔离库 `aitkp_hybrid` + OMLX `bge-m3-mlx-4bit` 上比较 Vector / BM25 /
  Weighted / RRF。Recall@5 分别为 0.917 / 0.944 / 0.972 / 0.972，MRR 分别为
  0.748 / 0.792 / 0.764 / 0.794；详见 `docs/HYBRID_SEARCH_EXPERIMENT.md`，
  manifest 为 `results/hybrid-phase9.json`、`results/hybrid-sweep-phase9.json`
- Phase 9 新增 `KnowledgeChunkService.hybrid_search()`，返回保留 `chunk_index`、
  `vector_rank`、`keyword_rank`、原始分数与 `matched_terms` 的 `FusedHit`。
  Vector / BM25 分别使用 `retrieval_vector_top_k` 与
  `retrieval_keyword_top_k` 作为候选深度；向量 SQL 增加身份 tie-break，
  Weighted 遵循候选声明的分数方向并拒绝混合方向 / 非有限数。
  该接缝尚未替换统一 `/search` 或 RAG；公开 API 的 score 契约仍未确定
- Phase 9 的实验表未建 HNSW，Query 延迟只含 pgvector 候选 + BM25 + 融合，
  不含 Query Embedding；6 条不可回答 Query 在四个通道上仍全部误召回。
  参数扫描中的最佳 MRR 来自同一份小样本，不能直接设为生产默认值。
  实验入口 `--fusion all` 比较四通道，`weighted` / `rrf` 只评测所选策略；
  manifest 的 `selection` / `evaluated_variants` 与 `default_fusion` 分开记录。
  `git_commit` 是生成提交，不一定是产物入库提交；当前结果在 `a73d963` 生成、
  随 `2c179e0` 入库
- Phase 10 已完成 Metadata Filter：新增受控过滤 DSL、SQLite / PostgreSQL
  跨方言谓词，以及 `search()` / `lexical_search()` / `hybrid_search()` 的
  `metadata_filters` 接缝。过滤在 `LIMIT` 前执行；不同字段 AND，数组字段
  contains-any；未知字段、空数组、空白值与非对象输入 fail fast
- Phase 10 使用独立数据集 `eval/fixtures/phase10_simulated.json`
  （22 条 Query / 47 Chunk，18 可回答 / 4 不可回答，26 条证据标注），
  每条 Query 都有 `expected_filters`。Phase 8 原数据集仍保留 24 条口径，
  历史 Phase 8/9 manifest 可继续追溯
- Phase 10 实验比较 `none / pre / post / during`：`pre` 先 SQL 过滤再排序，
  `post` 先取 Top-K 再过滤且不补候选，用于复现候选饥饿；`during` 当前与 `pre`
  相同，**不是**独立的 HNSW ANN 过滤实验。正式结果见
  `results/metadata-filter-phase10.json`，报告见 `docs/METADATA_FILTER.md`
- Phase 10 实验表未建 HNSW，延迟只含候选排序 / 融合，不含 Query Embedding；
  22 条 Query / 47 个 Chunk 只能验证语义与失败模式，不能作为生产阈值、ANN 或
  API P95 结论。统一 `/search` 与 RAG 尚未接入 Metadata Filter
- **Phase 5 的两个使用前提（新接手者最容易踩）**：
  1. 导入**不生成向量**：document Chunk 的 `embedding` 为空，必须补跑
     `uv run python scripts/index_entities.py --only document`（增量，不重建正文）
     才能参与向量检索；缺口看 `/health.metrics.chunks_without_embedding`；
  2. 重复导入同一 `doc_id` 会清掉已有向量，CLI 会打印
     `indexed_chunks_replaced=N` 提示需要重新补向量。
- 文档 Chunk 的强制重算入口：
  `uv run python scripts/index_entities.py --only document --rebuild`
  （切分策略或 Embedding 模型变更后使用；增量补向量不会刷新旧向量）。
- 导入空文件（解析出 0 Section）会保留该 `doc_id` 的旧 Document 元数据与 Chunk，
  不会清空索引。
- 文档：README / CHANGELOG 与代码同步

### 未完成 / 待办（按优先级）

1. Phase 12 的 100 条候选已完成，但必须人工逐条确认后才能作为正式 Gold Set；
   确认后复验 Phase 6 / 7 / 8 结论
2. Phase 13 检索指标已实现；等待 Phase 12 人工确认后在正式 Gold Set 上重跑
3. Phase 14 生成评测工具链已实现；等待人工确认数据与真实 LLM 输出后运行正式评测
4. Phase 15 / 16：等待人工确认数据与真实 LLM 配置后重跑；当前
   deterministic rewrite 未产生改写，不能据此判断收益
5. Phase 11：补齐正式 Rerank 实验报告与 manifest，比较 Hybrid 与
   Hybrid + Rerank 的 Recall@5 / MRR / 延迟
6. 在带 HNSW 的隔离表上补真正的 ANN filtering / iterative scan 实验
7. `Ling-3.0-tiny-oQ6e` 延迟较高；如需更低延迟，可评估同 OMLX 下的其他模型

### Phase 12 交接说明

- 候选文件：`eval/fixtures/phase12_candidates.json`（100 条，全部 `pending`）
- 数据契约：`app/evaluation/phase12_dataset.py`
- 生成 / 校验：`uv run python scripts/phase12_candidates.py --dry-run`、
  `uv run python scripts/phase12_candidates.py --check`
- 正式门槛：`uv run python scripts/phase12_candidates.py --check --mode formal`
  当前会因 pending 数据失败，这是预期行为
- 人工确认必须填写 `review_status=confirmed`、`reviewed_by=Terry`、带时区的
  `reviewed_at` 和 `review_notes`；不要用脚本批量伪造确认
- 当前未创建 `evaluation_cases` 数据库表；Phase 12 先以 JSON fixture 作为
  可复现资产，正式持久化留到需要在线管理评测集时

### Phase 13 交接说明

- 指标：`app/evaluation/metrics.py`，包含 Recall@K、Precision@K、MRR、
  二值 NDCG@K 与逐 Query Badcase 初筛
- 运行器：`app/evaluation/retrieval_evaluation.py`、
  `scripts/retrieval_evaluation.py`
- 报告：`docs/RETRIEVAL_EVALUATION.md`；manifest：
  `results/retrieval-phase13.json`
- 当前 manifest 为 `candidate` 模式，使用 Phase 8 的 24 条模拟 Query；
  `formal_gold_set=false`，不能当作正式 Gold Set 结论
- Phase 12 候选集可通过 `--phase12-dataset ... --mode candidate` 做流程演练；
  `--mode formal` 会拒绝全部 `pending` 数据

### Phase 14 交接说明

- 评测核心：`app/evaluation/generation_evaluation.py`
- 离线入口：`scripts/generation_evaluation.py`
- 响应夹具：`eval/fixtures/phase14_simulated_responses.json`（人工构造）
- 报告：`docs/GENERATION_EVALUATION.md`；manifest：
  `results/generation-phase14.json`
- 当前 manifest 为 `candidate` + `simulated`：10 条样本、5 个 Badcase，
  只验证指标与归因，不代表真实 LLM 质量
- `formal` 模式要求全部样本 `confirmed` 且带 `reviewed_by` / 带时区
  `reviewed_at`；不要伪造人工确认
- Deterministic 指标是回归基线，不等价于 Ragas；Ragas 适配层尚未固定版本、
  模型与参数，默认不可用

### Phase 15 交接说明

- 结构化契约：`app/query/understanding.py`
- 候选评测入口：`scripts/query_understanding_experiment.py`
- 候选 manifest：`results/query-understanding-phase12.json`
- 当前使用 Phase 12 的 100 条 `pending` Query，只报告 Schema 覆盖率、
  intent 分布与 expected filters 覆盖；Phase 12 的 `expected_intent` 是业务
  描述文本，不是受控枚举，因此不计算“意图准确率”
- `LLMQueryUnderstanding` 已实现 JSON 严格校验、重试与确定性降级；尚未用
  真实 LLM 做批量实验，不能把候选覆盖率当作模型效果

### Phase 16 交接说明

- 改写契约：`app/query/rewrite.py`
- 候选评测：`app/evaluation/query_rewrite_evaluation.py`、
  `scripts/query_rewrite_experiment.py`
- 候选 manifest：`results/query-rewrite-phase16.json`
- 当前比较 `noop` 与 `deterministic`；deterministic 没有改变任何 Phase 8
  Query，所以 Recall@5 / MRR 相同。这不说明 Rewrite 无效，也不说明有效
- 正式实验必须等待人工确认 Gold Set，并固定真实 LLM 的 Prompt、重试、
  延迟预算与成本边界；没有收益时应删除或保持默认 noop，不为“高级”而保留

### 已知取舍（README「已知取舍」表中有完整列表）

- 统一 `/search` 关键词仍走 `ILIKE`；BM25 与 Hybrid 已具备独立 Chunk 接缝，
  但尚未替换统一 API
- `tags` / `related_cases` 已支持 contains-any；PG 使用逐值 JSONB `@>` + OR，
  SQLite 使用 `json_each`。过滤语义跨方言一致，但大库仍需评估 GIN 与查询计划
- 统一 `/search` 暂无 `score` 字段；Chunk BM25 / Hybrid 在内部返回可解释分数，
  公开响应契约仍未确定
- BM25 每次查询读取候选 Chunk 并建内存索引，只适用于当前小规模验证；大库需
  持久化倒排表或独立搜索服务
- CI 目前覆盖 Ruff + SQLite pytest；PostgreSQL 集成测试仍为手工执行

---

## 6. 常用命令

```bash
uv sync                                   # 装依赖（自动建 .venv）
uv run pytest                             # 全量测试（默认 SQLite，无 Docker 可跑）
uv run ruff check .                       # 静态检查
uv run python scripts/query_understanding_experiment.py --dry-run
uv run python scripts/query_rewrite_experiment.py --dry-run
./scripts/db_init.sh                      # 起库 + 建表（需 Docker）
uv run uvicorn app.main:app --reload      # 起服务，文档 http://127.0.0.1:8000/docs
uv run alembic revision --autogenerate -m "..."   # 改 models 后生成迁移
uv run alembic upgrade head               # 应用迁移
```

---

## 7. 工作约定（接手者应延续）

- **TDD，先红后绿**。测试不依赖 Docker / 外部 API；涉及 Embedding/Rerank/LLM 的路径
  要么抽象出接口，要么在测试中显式跳过，不能让测试退化成需要联网才能跑。
- 改表先改 `app/models/`，再 autogenerate 迁移；永远不要手写 DDL。
- 新配置项：`app/core/config.py` 声明 + `.env.example` 同步，代码不出现硬编码密钥。
- 提交信息用约定式前缀（`feat:` / `fix:` / `refactor:` / `docs:` / `chore:`），
  且每个值得记录的变更都更新 `CHANGELOG.md` 的 `[Unreleased]` 段。
- 决策原因写进代码 docstring（现有代码如此，中文注释密度较高，保持一致）。
- 性质为"该做但没做"的欠账（如上面的 ILIKE、无 score）都显式记录在
  README 的已知取舍表与 CHANGELOG 的 Notes 里，而不是藏在代码里。

---

## 8. 给接手 agent 的第一步建议

1. `uv sync && uv run pytest` 确认默认路径测试通过（数量以实际输出为准）
2. 通读 `app/services/search_service.py` 和 `app/api/search.py` —— Phase 2/3 的接入点
3. 若 Docker 可用，跑 `TEST_DATABASE_URL=... uv run pytest` 复验真实 PostgreSQL
4. Phase 2/3/6/7/8/9/10 已完成；本机启动 OMLX 后运行
   `scripts/embedding_probe.py`、`scripts/ask.py` 与
   `scripts/chunking_experiment.py --dry-run`、
   `scripts/embedding_experiment.py --dry-run`、
   `scripts/lexical_experiment.py --dry-run`、
   `scripts/retrieval_evaluation.py --dry-run`、
   `scripts/generation_evaluation.py --dry-run`、
   `scripts/hybrid_experiment.py --dry-run`、
   `scripts/metadata_filter_experiment.py --dry-run` 做复验
