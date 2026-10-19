# Handover：AI Test Knowledge Platform 项目交接文档

> 写给接手的 agent / 开发者。读完本文 + `README.md` + `CHANGELOG.md` 即可上手干活。
> 更完整的背景：`docs/AI_Test_Knowledge_Platform_V2.md`（总体方案）、
> `docs/AI_Test_Knowledge_Platform_Implementation_Plan.md`（Phase 0–30 开发计划）。

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
- **Reranker**: bge-reranker-v2-m3；**LLM**: 本机 OMLX `Ling-3.0-tiny-oQ6e`（默认）↔ Ollama / 云 API
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
├── retrieval/   # Embedding 抽象；Phase 3+ 增加 Vector / Keyword / Hybrid Fusion
├── rerank/      # 空，Phase 11
├── generation/  # Phase 3 最小 RAG；Phase 17 扩展完整 Citation Pipeline
└── evaluation/  # Phase 6 数据集契约 + Recall@K / MRR；Phase 13 扩展 NDCG
alembic/         # 迁移脚本（versions/0001_initial_schema.py）
tests/           # 通过用例以当前实测为准（SQLite 默认路径）
eval/ data/      # 评测数据集 / 原始语料（均只有 .gitkeep，待填）
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

**分支 `phase-7-embedding`，Phase 0–7 核心链路已完成，数据库实机验证已完成。**

### 已完成

- Phase 0 完成：`PROJECT_SCOPE.md`（边界 / 非目标 / 指标）与 `LEARNING_QUESTIONS.md`
- Phase 0 之前的全部基础：目录骨架、依赖（uv + Python 3.11）、配置层、数据访问层、日志
- **Phase 1（FastAPI + PostgreSQL）全部完成，全程 TDD（先红后绿）**
  （当前 SQLite 355 passed + 7 skipped；真实 PostgreSQL 361 passed + 1 skipped）
  - ORM 四实体表（`test_cases` / `defects` / `requirements` / `documents`）+ 索引
  - Pydantic Schema（受控词表 + 入参规范化 + `to_orm_dict(mode="json")`）
  - 服务层：`EntityService` 基类（CRUD + 等值过滤 + 关键词 ILIKE + 分页）、四实体服务、
    `UnifiedSearchService` 跨实体统一检索
  - API：`GET /health`（含 DB 与 AI 能力自检）、四实体完整 CRUD、`GET /api/v1/search`
  - 统一错误信封 + `X-Request-ID` / `X-Process-Time-Ms` 中间件
  - Alembic 初始迁移 `0001`（含 `CREATE EXTENSION vector / pg_trgm`，仅 PG 分支执行）
- **PostgreSQL 17.11 实机验证通过**：Docker Compose 启动 `pgvector/pgvector:pg17`，
  Alembic 升级到 `0002 (head)`；`vector 0.8.7` / `pg_trgm 1.6` 已安装；
  专用测试库 `aitkp_test` 上 314 passed + 1 skipped；`GET /health` 返回数据库检查 `true`
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

1. 扩充 `eval/` 到约 100 条人工确认 Query，并在更大 Gold Set 上复验 Phase 6 / 7 结论
2. Phase 13 检索指标：补 Precision@K / NDCG 与 Badcase 归因
3. Phase 8：BM25 / Full Text Search，重点验证中文精确编号、版本号与关键词召回
4. `Ling-3.0-tiny-oQ6e` 延迟较高；如需更低延迟，可评估同 OMLX 下的其他模型

### 已知取舍（README「已知取舍」表中有完整列表）

- 中文关键词检索走 `ILIKE`（全表扫描），Phase 8 再定 zhparser / pg_jieba / 应用层分词
- `tags` / `related_cases` 数组暂不支持查询过滤（PG `text[] @>` 与 SQLite 不可兼得，Phase 10 补）
- 检索结果暂无 `score` 字段（Phase 9 融合打分后引入）
- CI 目前覆盖 Ruff + SQLite pytest；PostgreSQL 集成测试仍为手工执行

---

## 6. 常用命令

```bash
uv sync                                   # 装依赖（自动建 .venv）
uv run pytest                             # 全量测试（默认 SQLite，无 Docker 可跑）
uv run ruff check .                       # 静态检查
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
4. Phase 2/3/6/7 真实链路已完成；本机启动 OMLX 后运行
   `scripts/embedding_probe.py`、`scripts/ask.py` 与
   `scripts/chunking_experiment.py --dry-run`、
   `scripts/embedding_experiment.py --dry-run` 做复验
