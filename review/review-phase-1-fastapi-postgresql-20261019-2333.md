# 代码与设计 Review：AI Test Knowledge Platform

- 分支：`phase-1-fastapi-postgresql`
- 评审时间：2026-10-19 23:33（Asia/Shanghai）
- 评审范围：Phase 0 / Phase 1 全部，Phase 2（`knowledge_chunks` + Embedding + pgvector）首次落地，即 `97dc343`（HEAD）及其前的全部提交
- 对照文档：`docs/AI_Test_Knowledge_Platform_V2.md`（方案）、`docs/AI_Test_Knowledge_Platform_Implementation_Plan.md`（Phase 0–30）、`docs/PROJECT_SCOPE.md`（边界/非目标/指标）、`docs/handover.md`（工作约定）
- 复验结果：SQLite 默认路径 `216 passed, 3 skipped`；真实 PostgreSQL 17.11 + pgvector 0.8.7（`aitkp_test`）`218 passed, 1 skipped`；`uv run ruff check .` 全绿；`alembic check` 无漂移

---

## 1. 总体结论

整体质量高于预期：Phase 0–2 的实现与方案文档、项目边界、工作约定一致性良好，TDD、Schema 单一事实来源、Evidence First、错误信封、跨方言测试等纪律都被真正落到代码和测试里，而不是写在文档口号里。未发现阻断性问题。

值得警惕的是**纪律层面的三类偏差**：一是 Phase 2 收尾不完整（`IndexService` 无 HTTP/脚本入口、`get_embedding_client` 无调用方、`/api/v1/chunks` 只写不读）；二是**文档数字已经与代码脱节**（README/CHANGELOG/handover 仍写 192 个用例，实际 219）；三是 Phase 2 的验证边界（`docs/PROJECT_SCOPE.md` §8 明确要求配 Key 后用 bge-m3 重新验证）尚未闭环，而 `EMBEDDING_API_KEY` 仍为空。

按严重程度排序的发现见 §2–§5。

---

## 2. 阻断性问题

无。

---

## 3. 高优先级发现

### 3.1 `POST /api/v1/chunks` 唯一冲突返回 500，而非 409

- 位置：`app/api/chunks.py:21`、`app/services/knowledge_chunk_service.py:34`
- 现象：同一 `(source_type, source_id, chunk_index=0)` 重复 POST 时，`IntegrityError` 未被转换成 `DuplicateError`，全局 handler 兜成 `500 internal_error`（实测复现：`dup: 500 internal_error`）。服务层测试里也是直接断言 `pytest.raises(IntegrityError)`，等于把这个行为固化了。
- 影响：违反 handover §4.4「业务错误一律抛 `DomainError` 子类，409」的统一错误信封纪律；`KnowledgeChunkService` 是本项目中唯一绕开 `EntityService._commit()` 冲突转换的写路径。
- 建议：`KnowledgeChunkService.create/create_many` 的 commit 走与 `EntityService._commit()` 相同语义（捕获 `IntegrityError` → rollback → `DuplicateError`），或让 `KnowledgeChunkService` 复用 `EntityService`；同时把 `tests/services/test_knowledge_chunk_service.py:66` 的断言从 `IntegrityError` 改为 `DomainError`（code=`duplicate`）。

### 3.2 Phase 2 链路无入口：索引不可被触发，API 只写不读

- 位置：`app/services/index_service.py:110`、`app/api/chunks.py`、`app/retrieval/embedding.py:169`
- 现象：`IndexService.index_entities()` 与 `get_embedding_client()` 在 `app/` 内**没有任何调用方**，只在测试里被引用；`IndexService` 不继承 `EntityService`，也没挂路由；`/api/v1/chunks` 只有 `POST`，没有 `GET`/`DELETE`。
- 影响：方案 §7 的 `Source → … → Index` 管线在运行时不可用——「实体 → Chunk → 向量」只能靠测试驱动，无法用真实数据端到端验证；方案 §11 MVP 要求「 Recall@K / MRR 可复跑」，当前没有可复现的索引入口就无法做评测基线。
- 建议：按实现计划 Phase 3 的最小 RAG 顺序，先补一个明确的入口（`scripts/index_entities.py` 或 `POST /api/v1/index/rebuild`），再谈检索实验。不要只靠测试证明链路。

### 3.3 文档数字与代码脱节（纪律问题）

- 位置：`README.md:150`、`docs/handover.md:66`、`docs/handover.md:118`、`docs/handover.md:181`
- 现象：文档仍写「191 passed + 1 skipped / 192 个用例」，实测为 **SQLite 216 passed + 3 skipped，PG 218 passed + 1 skipped**。handover §7 明确要求「每个值得记录的变更都更新 CHANGELOG」，且「性质为欠账的项显式记录而不是藏着」。
- 影响：接手者按文档做基线校验时会得到不匹配的结果，误判为环境或代码问题；数字漂移本身就是「文档与代码双轨」的复现，与本项目反对双轨维护的原则冲突。
- 建议：更新 README/CHANGELOG/handover 的用例数，并在文档中标注「数字随用例增长，以 `uv run pytest` 实际输出为准」。

### 3.4 Phase 2 验证边界未闭环：真实 Embedding 未验证

- 位置：`docs/PROJECT_SCOPE.md` §8「Phase 2 验证边界」、`app/core/config.py:41`、`.env`
- 现象：`EMBEDDING_API_KEY` 为空，`/health` 的 `capabilities.embedding=false`；`FakeEmbeddingClient` 是唯一的实际执行路径。`docs/LEARNING_QUESTIONS.md` §11 把「bge-m3 在中文测试用例和缺陷上的实际召回表现」列为待验证问题，实现计划 Phase 2.1 也要求比较「支付失败 / 支付错误 / 登录成功」的相似度。
- 影响：当前**没有任何语义召回质量的结论**，而方案 §13 的成功标准（Recall@5 ≥ 85% 等）全部建立在真实 Embedding 之上。Fake 结果被写进 CHANGELOG「向量检索」条目时容易被误读为已验证。
- 建议：配 Key 后补一个不进默认测试集的语义实验脚本（如 `scripts/embedding_probe.py`），产出相似度矩阵并写进 `docs/`；在此之前，把 CHANGELOG 里 Phase 2 的向量检索条目标注为「链路已通，语义质量待验证」。

---

## 4. 中优先级发现

### 4.1 `/health` 的 `llm` 能力恒为 `true`，与自检语义不符

- 位置：`app/api/health.py:44`
- 现象：`capabilities` 中 `embedding` / `rerank` / `vector_search` 都做了实际探测，唯独 `"llm": True` 是硬编码；而 handover §2 明确说本机 Ollama「尚未 pull 任何模型」，即 LLM 实际不可用。
- 影响：违反该文件 self-check 的设计意图（「未配置 Key 时提前暴露比调用时才报错更友好」），会误导调用方以为生成链路可用。
- 建议：改成与其余字段一致的探测逻辑（对 Ollama 走 `GET /v1/models` 或至少按 `llm_provider` 判定），或在返回里补 `llm_model` / 探测状态说明。

### 4.2 统一检索的分层过滤参数不一致，且不支持 `severity`

- 位置：`app/api/search.py:33-48`、`app/services/search_service.py:76`
- 现象：`/api/v1/search` 支持 `module / version / priority / status / type`，但**不支持 `severity`**，而 `SearchHit` 却输出 `severity` 字段；`/api/v1/defects` 的过滤参数是用 `Severity` / `Priority` / `DefectStatus` 枚举类型声明的，`/api/v1/search` 的 `priority` 用 `Priority`、`status` 却是裸 `str`（文档描述「取规范值如 open / fixed / approved」，不传枚举）。
- 影响：缺陷检索是方案 §3 的核心场景（「支付失败相关的 P0 缺陷」），按严重程度过滤在 MVP 用例里是自然需求，当前缺失；`status` 的裸 `str` 也失去了受控词表归一化（如 `"OPEN"` 会被原样传下去）与 OpenAPI 文档价值。
- 建议：补齐 `severity` 过滤并统一用枚举字段类型（与 `defects.py` 一致），或直接说明「统一检索只暴露跨实体公共维度」这一取舍并写进 README 已知取舍表。

### 4.3 查询参数枚举不归一化，入参与写参行为不一致

- 位置：`app/api/testcases.py:38`、`app/api/search.py:36`
- 现象：写路径（`TestCaseCreate`）用 `PriorityField`，入参 `"p0"` 可归一化；查路径（`list` / `search`）直接用 `Priority`，实测 `?priority=p0` → **422**，`?priority=P0` → 200。同理 `search?type=Smoke` → 422，`type=smoke` → 200。
- 影响：违反 handover §4.3「Pydantic 负责大小写归一化与空白清洗」的一致性预期；调用方（含未来的 Query Understanding / Agent 输出）很容易传小写值而拿到 422。
- 建议：查询参数也用 `PriorityField` / `CaseTypeField` 等规范化字段类型；至少补一个「小写枚举被归一化」的 API 测试固定行为。

### 4.4 检索参数配置尚未被任何代码消费

- 位置：`app/core/config.py:63-67`（`retrieval_vector_top_k` / `retrieval_keyword_top_k` / `retrieval_fusion` / `retrieval_rrf_k` / `retrieval_final_top_k`）
- 现象：这 5 个配置项在 `app/` 内无引用（只有 `.env.example` 声明）。
- 影响：`docs/LEARNING_QUESTIONS.md` §5 强调「TopK 是实验起点，不是永久常量；Chunk/Embedding/Filter 变化后必须重新调」——配置先于实现是合理的（Phase 8/9/11 会消费），但目前属于「声明了却没人读」的状态，容易被误认为已生效。
- 建议：保留（方向正确），但在 config 注释或 README 中标注「Phase 9/11 起生效」，避免接手者以为改配置就能改变当前检索行为。

### 4.5 依赖声明超前，且缺少 lint/format 与 CI 保障

- 位置：`pyproject.toml:9-30`、`pyproject.toml:52-57`
- 现象：`pandas` / `openpyxl` / `markdown-it-py` / `pypdf` / `numpy` / `tenacity` / `httpx` 在 `app/` 与 `tests/` 中**均无 import**（Phase 5 / 11 才用）；`pyproject.toml` 的 ruff 未启用 `format` 相关配置，仓库也没有任何 CI 配置（无 `.github/workflows`、无 pre-commit）。
- 影响：依赖超前本身可接受（实现计划 Phase 5/11 明确要用），但与「不为没评测收益的复杂度买单」的原则略有张力；更重要的是纪律类保障依赖自觉——目前 PG 集成验证只能靠人工设 `TEST_DATABASE_URL` 执行，CHANGELOG/handover 声称的「PG 192 passed」无法自动复验。
- 建议：加一条最小 CI（默认 SQLite 全量 + ruff），PG 路径保留手工触发；依赖可在 Phase 5 接入时再补，或加注释说明用途。

---

## 5. 低优先级 / 一致性观察

- **HNSW 漂移检查靠字符串匹配**：`tests/test_migrations.py:78` 用 `"USING hnsw" in sql` 解析 `op.execute` 里的 SQL 来识别索引名。这是 ast 静态解析的务实妥协（PG 专属索引无法在 SQLite 上反射），但字符串匹配很脆：改写 SQL 格式（如 `IF NOT EXISTS`、换行变化）就会静默漏检。建议在迁移脚本里加一行约定注释（如 `# index: idx_knowledge_chunks_embedding_hnsw`）并让解析器读注释，降低耦合。
- **HNSW 索引在 PG 上无条件创建**：`alembic/versions/0002_knowledge_chunks.py` 未声明 `m`/`ef_construction` 参数（用 pgvector 默认值）。当前数据量下可接受，但 `docs/LEARNING_QUESTIONS.md` §11 已把「HNSW 参数对召回率和延迟的影响」列为待验证项；后续调参要记得它是实验变量，且调参会改变 Recall，必须重跑评测。
- **`docker-compose.yml:19` 注释与配置不匹配**：注释写「中文全文检索所需的默认分词配置（zhparser 需另行安装镜像）」，实际 `POSTGRES_INITDB_ARGS` 只有 `--encoding=UTF8 --locale=C`，与 zhparser 无关。README「已知取舍」已说明中文全文检索走 `ILIKE`、Phase 8 再定方案，此处注释容易误导。
- **空包目录 `ingestion` / `rerank` / `generation` / `evaluation` 的 `__init__.py` 全为 0 字节**：对齐方案 §14 目录布局是好事，但 0 字节文件没有说明「该目录归属哪个 Phase」。建议在各自 `__init__.py` 里写一行 docstring（如 `"""重排（Phase 11）：…"""`），让目录结构自带 roadmap 信息。
- **`SourceType` 在 `schemas/search.py` 与 `schemas/knowledge_chunk.py` 各定义一次**（同内容 `Literal`）：当前一致且测试能兜住，但 Phase 3 引入 RAG 后两处必须同步演进，建议收敛到一处（如 `schemas/common.py`）再分别 re-export。
- **`EmbeddingError` 的 HTTP 503 与 `/health` 的 false 双保险**做得对，但 `app/retrieval/embedding.py:92` 的维度不一致只是 `logger.warning`（写库时才会被 `_validate_dimension` 拒绝）。这是可接受的「fail late 但有明确错误」设计，建议保持并在 Phase 7 换 Embedding 模型时优先改成启动即失败。
- **`docs/PROJECT_SCOPE.md` §8 的 Phase 映射未随 Phase 2 推进更新**：仍写「Phase 2 进行中」。按 handover「文档与代码同步」的约定，Phase 2 核心能力落地后应把状态推进并同步 `eval/`、真实 Embedding 的待办。

---

## 6. 纪律符合性逐项核对

| 纪律（来源） | 结论 | 依据 |
|---|---|---|
| TDD 先红后绿（handover §7） | 符合 | Phase 1/2 提交序列为 `feat(models)` → `feat(schemas)` → … ，测试与实现同提交；219 个用例覆盖模型/类型/Schema/服务/API/迁移 |
| 测试不依赖 Docker / 外部 API（handover §7） | 符合 | `tests/conftest.py` 默认 SQLite 内存库；Embedding 用确定性 `FakeEmbeddingClient`；PG 专属用例显式 `pytest.skip`（3 处） |
| PostgreSQL 专属能力必须有 PG 实机验证（PROJECT_SCOPE §7） | 符合 | `tests/test_retrieval_pg.py`、`tests/test_migrations.py::test_postgresql_extensions_present`；本次 PG 复验 218 passed |
| Schema 单一事实来源（handover §4.1） | 符合 | `sql/init` 已删除；`alembic/versions/0001/0002` 复用 `app/models/types.py`；`tests/test_migrations.py` 做 ORM↔迁移漂移检查；本次 `alembic check` 无漂移 |
| 跨方言类型集中在 `types.py`（handover §4.2） | 符合 | `app/` 与迁移内无 `if dialect == "postgresql"` 的业务判断（仅 `types.py` 加载方言实现、`0002` 建 HNSW、`health` 能力探测） |
| 枚举用 TEXT + StrEnum（handover §4.3） | 符合 | `app/models/enums.py` 全 `StrEnum`；`to_orm_dict(mode="json")` 转裸值 |
| 统一错误信封（handover §4.4） | 基本符合，一处破例 | `app/api/errors.py` 信封统一；**但 chunk 唯一冲突走 500**（§3.1） |
| 公开 API 用业务编号寻址、编号不可改（handover §4.5） | 符合 | `/{case_id}` `/{bug_id}` `/{req_id}` `/{doc_id}`；`ORMUpdate` 的 `extra="forbid"` 拦住改编号 |
| Evidence First：无打分依据不返回 score（handover §4.6 / PROJECT_SCOPE §5） | 符合 | `SearchHit` 无 `score`；`matched_on` 说明召回原因；`KnowledgeChunkRead` 不返回向量 |
| 原始实体与检索单元分离（handover §4.7 / 方案 §5） | 符合 | `knowledge_chunks` 只存 `source_type`/`source_id`/`content`/`meta`/`embedding`/`content_hash`；不建外键 |
| 核心 Pipeline 手写，不用 LangChain/LlamaIndex（PROJECT_SCOPE §6） | 符合 | 无相关依赖；`app/retrieval` 为自写抽象 |
| 不引入 Milvus（PROJECT_SCOPE §6） | 符合 | 仅 pgvector |
| 决策原因写进 docstring（handover §7） | 符合（密度高） | `types.py`、`search_service.py`、`knowledge_chunk_service.py`、`base.py` 等均有取舍说明 |
| 约定式提交 + CHANGELOG 同步（handover §7） | 基本符合 | 11 个提交全部 `feat:/docs:/refactor:/chore:`；CHANGELOG `[Unreleased]` 有 Phase 0/1/2 条目；**但用例数等数字未同步**（§3.3） |
| 欠账显式记录（handover §7） | 符合 | README「已知取舍」表覆盖 ILIKE、无 score、数组过滤、编号不可变；CHANGELOG `Notes` 同步 |

---

## 7. 与开发计划（Phase 0–30）的对齐度

| 阶段 | 计划要求 | 当前状态 | 判断 |
|---|---|---|---|
| Phase 0 | `PROJECT_SCOPE.md` + `LEARNING_QUESTIONS.md`（10 问） | 已完成，10 问全部有「项目含义」落地段，另加 §11 待验证问题 | 对齐，质量优于要求 |
| Phase 1 | FastAPI + PostgreSQL，产出 `/health`、四实体 CRUD、`/search` | 全部完成，另有错误信封、中间件、Alembic、PG 实机验证 | 对齐 |
| Phase 2 | Embedding 实验 + pgvector（产出 `ORDER BY embedding <=> query` 的向量检索） | ORM/迁移/Embedding 抽象/`IndexService`/`KnowledgeChunkService.search` 已落地；**真实 Embedding 语义实验（2.1）未做** | 部分对齐（见 §3.4） |
| Phase 3 | 手写最小 RAG（`ingest.py` / `query.py`） | 未开始；且 Phase 2 无索引入口（§3.2）会拖慢它 | 待开始 |
| Phase 4 | 测试知识模型 + `docs/WHY-STRUCTURED-KNOWLEDGE.md` | 模型已建，**文档未产出**（计划明确列为产出物） | 欠账，建议补 |
| Phase 5–11 | 数据接入 / Chunking / BM25 / Hybrid / Metadata Filter / Rerank | 未开始；`app/ingestion`、`app/rerank` 为空包 | 待开始 |
| Phase 12–13 | ~100 条评测集 + Recall@K / MRR | `eval/` 只有 `.gitkeep`；`app/evaluation` 为空 | 待开始 |
| Phase 28–30 | 架构/Evaluation/Badcase 报告与最终验收 | 未开始 | 待开始 |

方案 §11「MVP 必须完成」清单中，当前仅满足「PostgreSQL + pgvector」「Embedding（抽象已建，语义未验）」「Vector Search」「Metadata Filter（等值，非 Phase 10 完整版）」「FastAPI」「README」几项；`eval/` 数据集与指标是最大的空档。按 Phase 29 的 8 周计划，当前进度大致落在 Week 1–2 之间，与「Phase 1 完成 / Phase 2 进行中」的自述一致。

---

## 8. 建议的下一步（按优先级）

1. 修 §3.1：chunk 唯一冲突改 409，并同步测试断言（影响错误契约，最应优先）。
2. 补 §3.2 索引入口（脚本或路由），让「实体 → Chunk → 向量」能在真实数据上跑通。
3. 同步 §3.3 文档数字，并把 CHANGELOG 里 Phase 2 的向量检索标注为「语义质量待验证」。
4. 配 `EMBEDDING_API_KEY` 后做 §3.4 的 bge-m3 语义探针实验，把结果写进 `docs/`（这是 Phase 2 能否算「完成」的门槛）。
5. 修 §4.1 `/health` 的 `llm` 硬编码，并统一 §4.2/§4.3 的查询参数枚举与 `severity` 过滤。
6. 加最小 CI（SQLite 全量 + ruff），把「纪律」从人工约定变成自动门禁。
7. 补 Phase 4 的 `docs/WHY-STRUCTURED-KNOWLEDGE.md`（计划中明确的产出物，成本极低）。
