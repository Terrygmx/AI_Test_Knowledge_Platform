# Changelog

本项目所有值得记录的变更都写在这里。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased]

### Added

#### Phase 0：项目边界（2026-10-19）

- 新增 `docs/PROJECT_SCOPE.md`：明确问题、用户、数据、MVP 用例、非目标、成功指标与
  Retrieval / Generation 边界。
- 新增 `docs/LEARNING_QUESTIONS.md`：记录 Embedding、Chunk、Vector / BM25、Rerank、TopK、
  Recall@K / MRR、Faithfulness、RAG 幻觉、适用边界与持续优化问题。

#### Phase 2：Embedding + pgvector（2026-10-19）

- **检索单元**：新增 `knowledge_chunks` ORM / Schema / 迁移 `0002`，包含来源类型、
  来源编号、文档编号、Chunk 序号、正文、Metadata、1024 维向量、内容哈希与版本。
- **向量索引**：PostgreSQL 上使用 `vector(1024)` 与 HNSW `vector_cosine_ops`；
  SQLite 默认测试库仍可建表，但不支持距离运算。
- **Embedding 抽象**：新增 `EmbeddingClient` 协议、OpenAI-compatible 客户端、
  确定性 Fake 客户端、批量维度校验与 SHA-256 内容哈希。
- **索引服务**：TestCase / Defect / Requirement / Document 到 Chunk 的映射，
  支持单条与批量 Embedding 入库。
- **向量检索（链路已通，语义质量待验证）**：`KnowledgeChunkService.search()` 在
  PostgreSQL 上按 cosine distance 返回候选和距离，并支持来源类型过滤；
  SQLite 下明确拒绝而不是返回假排序。Fake Embedding 结果不能作为召回质量结论。
- **索引入口**：`scripts/index_entities.py` 支持全量重建 / `--dry-run` / 按来源过滤；
  先删除该来源旧 Chunk 再重建，重复执行安全；Document 正文解析留到 Phase 5。
- **语义探针**：`scripts/embedding_probe.py` 需真实 `EMBEDDING_API_KEY`，不进入默认测试。
- **Chunk API**：补 `GET /api/v1/chunks`、`DELETE /api/v1/chunks/{id}`；重复写入返回 409。
- **注意**：Fake Embedding 只证明链路可运行，语义召回质量尚未验证。

#### Phase 3：手写最小 RAG（2026-10-20）

- **LLM 抽象**：`LLMClient` + OpenAI-compatible 实现，测试用 Fake，不引入 LangChain。
- **Context Assembly**：按检索顺序生成 `[1] [2]` 证据块，限制单 Chunk / 总上下文字符数。
- **Answer + Citation**：`POST /api/v1/rag/answer`；回答引用编号必须来自本次证据；
  `citation_valid` 只表示引用编号合法，不冒充 Faithfulness；无检索结果时返回
  `evidence_sufficient=false` 与“证据不足”，且不构造 / 调用 LLM。
- **可观测性**：Citation 返回 `rank` / `distance`；支持可选 `max_distance` 阈值；
  `/health.metrics.chunks_without_embedding` 暴露未完成索引的 Chunk 数。
- **命令行入口**：`scripts/ask.py` 支持 `--top-k`、`--source-type` 与 `--max-distance`。
- **验证边界**：Phase 3 只做 Vector Search + TopK，Hybrid / Rerank 仍在后续 Phase。
- **Review 修复**：拆分 `citation_valid` / `evidence_sufficient`，不再用格式合法冒充
  Faithfulness；Citation 暴露 `rank` / `distance`；上下文预算首块也受约束；
  引用解析支持 `[1, 2]` / `[1 ]`；LLM 健康探测改为 60s TTL；
  Chunk 支持 `has_embedding` 过滤，`/health` 暴露缺向量计数。
- **本机 Embedding**：默认切换到 OMLX `bge-m3-mlx-4bit`
  （`http://127.0.0.1:28080/v1`，Key `omlx`），完成真实相似度、索引和
  pgvector 检索验证，详见 `docs/EMBEDDING_EXPERIMENT.md`。
- **本机 LLM + Phase 3 真实链路**：默认 LLM 切换到 OMLX
  `Ling-3.0-tiny-oQ6e`（同一地址与 Key），完成 `scripts/ask.py` 与
  `POST /api/v1/rag/answer` 的 PostgreSQL + pgvector 端到端验证；
  修复“部分回答带合法引用、末尾又写证据不足”时引用被整体丢弃的问题。
- **Phase 3 Review 修复**：`/health` 对 Embedding / LLM 均探测 `/models`
  并校验配置模型存在，服务不可达或模型名错误不再误报 `true`；
  `evidence_sufficient` 只表示是否通过 `max_distance`，与引用合法性解耦；
  无引用回答统一降级为“证据不足”，不再原样返回；新增 `RAG_REQUEST_TIMEOUT`
  （默认 30 秒）与实验性慢接口说明；Citation snippet 截断、`retriever` 收敛为
  受控值，CLI 输出 rank / distance；同步 OMLX / Key / Phase 状态文档。

#### Phase 4：测试知识模型收口（2026-10-20）

- **模型契约验收**：确认 TestCase / Defect 字段、受控词表、唯一业务编号、
  Alembic Schema 与索引完整；知识模型边界见 `docs/PHASE4_KNOWLEDGE_MODEL.md`。
- **Metadata 补齐**：TestCase Chunk 保留 `sub_module` / `case_type` / `tags`；
  Defect Chunk 保留 `related_cases`，为 Phase 10 Metadata Filter 提供完整字段。
- **关联用例校验**：`related_cases` 与业务编号使用相同格式规则，非法编号返回 422；
  仍保持弱关联，不要求关联用例已存在。
- **边界保护**：实体转 Chunk 时容忍尚未 flush 的 ORM 实例中数组字段为 `None`。

#### Phase 5：数据接入（2026-10-20）

- **表格导入**：新增 CSV / Excel 读取、表头归一化、实体类型识别、空值清洗、
  数组字段拆分、文件内去重与按业务编号幂等更新；整文件事务保证失败不留半批数据。
- **Markdown**：新增 front matter、标题树与 Section 解析；正文保留原始 Markdown，
  每个 Section 生成一个 Document Chunk，重复导入按文档替换旧 Chunk。
- **PDF**：使用 `pypdf` 抽取普通文本层，无文本 PDF 明确报错；OCR / 表格 / 版面还原
  不在当前范围。
- **CLI**：新增 `scripts/ingest.py table|markdown|pdf`。
- **文档**：新增 `docs/PHASE5_INGESTION.md`，说明表头、清洗、幂等与边界。

#### Review 修复：Phase 3 / Phase 5（2026-10-20 02:00）

- **无标题文档不再丢正文**：`MarkdownParser` 在无标题时整篇回退为单个 Section，
  首个标题之前的“前言”也保留为 `前言` Section。修复前纯文本 PDF（无标题）会
  导入“成功”却产出 0 个 Chunk，正文整体丢失且无报错。
- **document Chunk 补向量入口**：`KnowledgeChunkService.backfill_embeddings()`
  与 `scripts/index_entities.py --only document` 给 `embedding IS NULL` 的 Chunk
  增量补向量（不重建正文）；`--only testcase,defect,requirement` 仍是重建语义。
  修复前 `docs/PHASE5_INGESTION.md` 承诺的入口实际不认 `document`。
- **重复导入不再静默清向量**：`MarkdownImportResult.indexed_chunks_replaced`
  带回被替换的已向量化 Chunk 数，服务层写 warning，CLI 打印补救命令。
- **RAG 超时真正释放请求**：改用模块级共享线程池（原先每请求 `with
  ThreadPoolExecutor`，退出时 `shutdown(wait=True)` 会把工作时间完整算进请求耗时），
  并加 `RAG_MAX_CONCURRENCY` 并发闸门，满载立即 503。
- **超时预算不再颠倒**：`Settings` 强制 `LLM_TIMEOUT <= RAG_REQUEST_TIMEOUT`
  （默认 LLM 30 秒 / 请求 45 秒，按实测 30 秒生成耗时留余量）；
  README / OpenAPI 如实说明 503 只是提前放弃等待，底层调用无法被中断，
  会继续占用线程到 `LLM_TIMEOUT`。
- **CSV 导入 document 不再丢正文**：`documents` 表无 `content` 列，正文改为写入
  `document` Chunk（按 `doc_id` 替换）；`Type` 列在文档语义下归一为 `doc_type`。
  修复前该列被 Pydantic 静默忽略，正文无声丢失。
- **`/models` 探测可排查**：探测失败 / 未命中模型时写 warning 日志，不再静默吞异常；
  文档标注该探测依赖上游实现 `GET /models` 且模型名精确匹配。
- **`--pairs` 参数删除**：`scripts/embedding_probe.py` 只保留内置测试对这一种模式，
  未知参数显式提示被忽略。

#### Review 修复：RAG 并发 / 补向量分批 / 空文档（2026-10-20 03:00）

- **满载与超时语义分离**：并发超过 `RAG_MAX_CONCURRENCY` 返回 429 +
  `rag_overloaded`（带 `Retry-After: 1`），不再与超时共用 503 + `llm_error`。
  修复前调用方要区分两者只能匹配中文文案；满载应当退避重试，而不是当上游故障处理。
- **后台任务自带会话**：RAG 推理改在守护线程里执行，并通过可注入的会话工厂自己
  开 / 关 Session。修复前把请求级 Session 交给后台任务，超时后（真实连接池下）
  会话会被回收复用，属于「现在没炸、换配置就炸」。
- **进程退出不再被推理拖住**：工作线程改为 daemon，删掉模块级线程池。修复前
  非守护线程池会让 `uvicorn --reload` / 退出等待任务跑完（最长 `LLM_TIMEOUT`）。
- **补向量分批提交**：`backfill_embeddings` / `reembed_chunks` 按
  `EMBEDDING_BATCH_SIZE` 用主键游标分批读取、生成并逐批 commit。修复前一次性读入
  全部 Chunk 且一次 commit 覆盖全部，上游在第 N 批失败时已算出的向量一起回滚，
  大批量补向量等于「要么全成功要么全无」。
- **document 显式重算入口**：新增 `KnowledgeChunkService.reembed_chunks()` 与
  `scripts/index_entities.py --only document --rebuild`。切分策略（Phase 6）或
  Embedding 模型变更后，增量补向量不会刷新旧向量，之前只能手工清表重导。
- **空文档不清索引**：解析出 0 个 Section 时保留该 `doc_id` 的旧 Document 元数据
  与 Chunk 并记 warning，CLI 打印提示。修复前导入一个空文件等于静默清空已有索引，
  还会把标题 / version / path 覆盖成空解析结果。
- **测试整理**：移除与 `test_timeout_returns_before_worker_finishes` 重复的旧超时用例；
  补齐「后台任务不用请求级 Session」「批次失败不回滚」「CLI 提示分支」三处测试。

#### Phase 1：FastAPI + PostgreSQL（2026-10-16，TDD：先写失败测试再实现）

- **ORM 实体**（`app/models/`）：`test_cases` / `defects` / `requirements` / `documents` 四张业务实体表，
  索引命名与迁移一致；`TestCase` 等实体是「原始事实来源」，检索单元 `knowledge_chunks` 留给 Phase 2。
- **可移植列类型**（`app/models/types.py`）：`pk_type()` / `TextArray` / `JSONMap` 按方言分流
  （PG → `BIGSERIAL` / `TEXT[]` / `JSONB`，SQLite → `INTEGER` / JSON 文本），
  使整套测试无需 Docker 即可运行。
- **受控词表**（`app/models/enums.py`）：`Priority` / `CaseType` / `Severity` / `DefectStatus` / `RequirementStatus`，
  统一用 `StrEnum`（`str(member)` 即规范值，避免写入 `"Priority.P0"` 这类脏数据）；
  数据库列保持 `TEXT`，避免 PG 原生 enum 的 `ALTER TYPE` 迁移成本。
- **Pydantic Schema**（`app/schemas/`）：入参统一去空白、枚举大小写归一化、标签去重去空；
  `to_orm_dict()` 用 `mode="json"` 把枚举转成裸值；更新入参 `extra="forbid"`（拼错字段名 / 改不可变业务编号直接 422）。
- **服务层**（`app/services/`）：通用 `EntityService` 基类（CRUD + 等值过滤 + 关键词检索 + 分页），
  四个实体服务只声明差异；`UnifiedSearchService` 提供跨实体统一检索。
- **API**（`app/api/`、`app/main.py`）：`GET /health`（含数据库与 AI 能力自检）、
  `/api/v1/{testcases,defects,requirements,documents}` 完整 CRUD、`GET /api/v1/search`；
  公开接口统一用业务编号（Case ID / Bug ID）寻址。
- **统一错误处理**（`app/api/errors.py`）：所有错误都是 `{"error": {code, message, request_id, detail}}` 信封；
  领域异常（`app/core/exceptions.py`）→ 404 / 409，参数校验 → 422，未处理异常 → 500 且不泄露内部细节。
- **请求中间件**（`app/api/middleware.py`）：`X-Request-ID`（可传入，便于串联上层日志）+
  `X-Process-Time-Ms` + 访问日志，为后续 Badcase 分析打底。
- **Alembic**：`alembic.ini` + `alembic/env.py` + 初始迁移 `0001_initial_schema.py`
  （含 `CREATE EXTENSION vector` / `pg_trgm`，仅 PG）。
- **测试**：覆盖模型形状、Schema 规范化、服务 CRUD/过滤/分页、API 全流程、
  错误信封、中间件、迁移漂移与迁移可执行性。
- **PostgreSQL 实机验证**：新增 PG 专属迁移测试，在真实 PostgreSQL 上从 base 升级到
  `head` 并校验 `vector` / `pg_trgm` 扩展；SQLite 默认路径自动跳过该用例。

### Changed

- **Schema 改由 Alembic 单一管理**：删除 `sql/init/01_schema.sql` 与 docker-compose 的初始化脚本挂载。
  原因：DDL 文件与 ORM 双轨维护必然漂移；现在 `app/models/` 是唯一事实来源，
  `alembic/versions/` 是它的版本化投影，`tests/test_migrations.py` 负责盯住两者一致。
- `docker-compose.yml` 不再挂载 `sql/init`；新增 `scripts/db_init.sh`（起容器 + 等 healthy + 跑迁移）。
- `app/core/config.py` 新增 `api_prefix`（默认 `/api/v1`），`.env.example` 同步补充 `API_PREFIX`。
- `README.md` 重写：目录结构、数据库初始化、测试策略（SQLite 默认 / `TEST_DATABASE_URL` 切 PG）、当前状态与已知取舍。

### Removed

- `sql/` 目录（初稿 DDL 内容已并入 Alembic 初始迁移，未丢失）。

### Notes

- **PostgreSQL 17.11 实机验证通过**：`pgvector/pgvector:pg17` 容器运行正常，
  Alembic 升级到 `0001 (head)`，`vector 0.8.7` / `pg_trgm 1.6` 已安装；
  `aitkp_test` 上 231 passed + 1 skipped，SQLite 默认路径为 229 passed + 3 skipped。
- **Docker 环境**：首次启动需拉取 `pgvector/pgvector:pg17` 镜像；
  测试库 `aitkp_test` 可重复使用，但测试会 `drop_all` + `create_all`，不要指向开发库。
- **中文全文检索待定**：当前关键词检索走 `ILIKE`（全表扫描），Phase 8 再定 zhparser / pg_jieba / 应用层分词。
- **检索结果暂无 score 字段**：没有打分依据时不编数字（Evidence First），Phase 9 融合打分后引入。
- **`.env` 中 `EMBEDDING_API_KEY` / `RERANK_API_KEY` 为空**：`/health` 的 `capabilities` 会显示 false，
  涉及 Embedding / Rerank 的代码路径暂不可运行；LLM 能力也改为实际探测，而不是恒为 true。
- **`tags` / `related_cases` 暂不支持查询过滤**：PG 的 `text[] @>` 与 SQLite 不可兼得，Phase 10 元数据过滤时补。

### 历史（Phase 0 之前）

- 项目目录骨架，对齐方案 §14 的 `app/` 布局（`core` / `api` / `ingestion` / `retrieval` / `rerank` / `generation` / `evaluation` / `models`）。
- 依赖声明 `pyproject.toml`：FastAPI、SQLAlchemy、Alembic、psycopg3、pgvector、openai、pandas、pypdf、pytest、ruff 等。
- 虚拟环境：`uv sync` + Python 3.11（`.python-version`）。
- 配置层 `app/core/config.py`：基于 pydantic-settings，统一从 `.env` 读取应用 / 数据库 / Embedding / Rerank / LLM / 检索参数。
- 数据访问骨架 `app/core/db.py`：SQLAlchemy 2.0 engine、session、`session_scope()`、`ping()`。
- 日志骨架 `app/core/logging.py`：统一格式，为后续 Badcase 阶段日志打底。
- `.env.example`：Embedding / Rerank / LLM 双通道（Ollama ↔ 云 API）配置模板。
- `.gitignore`：排除 `.venv`、`.env`、`data/`、`.workbuddy-ai/` 等。
