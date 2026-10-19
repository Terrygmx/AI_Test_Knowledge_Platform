# AI Test Knowledge Platform

面向测试工程的知识智能平台：统一接入测试用例、缺陷、需求/产品文档与测试规范，
通过结构化知识建模 + Hybrid Retrieval（Vector + Keyword + Metadata Filter）+ Rerank + RAG，
为测试人员以及后续的智能回归测试 Agent 提供**可追溯**的知识检索与问答能力。

> **Retrieval First** —— 先证明检索正确，再做生成。
> **Evidence First** —— 回答必须有来源，不允许凭空生成 Case ID / Bug ID。

详细方案见 [`docs/AI_Test_Knowledge_Platform_V2.md`](docs/AI_Test_Knowledge_Platform_V2.md)，
开发计划见 [`docs/AI_Test_Knowledge_Platform_Implementation_Plan.md`](docs/AI_Test_Knowledge_Platform_Implementation_Plan.md)；
项目边界与学习问题见 [`docs/PROJECT_SCOPE.md`](docs/PROJECT_SCOPE.md)、
[`docs/LEARNING_QUESTIONS.md`](docs/LEARNING_QUESTIONS.md)。

---

## 技术栈

| 层 | 选型 |
|---|---|
| 语言 | Python 3.11 |
| Web / API | FastAPI + Uvicorn |
| 数据校验 | Pydantic v2 + pydantic-settings |
| 数据库 | PostgreSQL 17 + pgvector（关系数据 / Metadata / 全文检索 / 向量 四合一） |
| ORM / 迁移 | SQLAlchemy 2.0 + Alembic |
| 检索 | Vector(HNSW) + 全文检索 + RRF/Weighted 融合 + Metadata Filter |
| Embedding | bge-m3（OpenAI-compatible API，1024 维） |
| Reranker | bge-reranker-v2-m3（OpenAI-compatible API） |
| LLM | OpenAI-compatible：本机 OMLX / Ollama / 云 API，`.env` 一键切换 |
| 评测 | 自研指标（Recall@K / Precision@K / MRR / NDCG）+ 后续接 Ragas |
| 工程 | uv + pytest + ruff |

**V1 不引入 Milvus。** 测试数据具有强结构化特征，PostgreSQL 单库即可同时承担关系数据、
Metadata 过滤、全文检索与向量检索，避免多套存储带来的数据一致性问题。

---

## 目录结构

```
AI_Test_Knowledge_Platform/
├── app/
│   ├── core/           # 基础设施：配置、数据库会话、日志、领域异常、请求上下文
│   ├── api/            # FastAPI 路由层 + 中间件 + 统一错误处理
│   ├── models/         # ORM 实体（Schema 的单一事实来源）+ 可移植列类型 + 受控词表
│   ├── schemas/        # Pydantic 请求 / 响应模型
│   ├── services/       # 业务服务层：CRUD 基类 + 实体服务 + 统一检索
│   ├── ingestion/      # 数据接入：Parser → Normalizer → Knowledge Model → Chunker（Phase 5）
│   ├── retrieval/      # 检索：Embedding / Vector / Keyword / Hybrid Fusion / Metadata Filter
│   ├── rerank/         # 重排（Phase 11）
│   ├── generation/     # 上下文组装 + LLM + Citation（Phase 17）
│   └── evaluation/     # 离线评测指标与运行器（Phase 13）
├── alembic/            # 迁移脚本（env.py + versions/）
├── docs/               # 项目方案、开发计划、各阶段实验报告
├── eval/               # 评测数据集（约 100 条人工确认 Query）
├── scripts/            # 数据导入、索引、实验脚本
├── tests/              # 测试代码
├── data/               # 原始语料与产物（不入库）
├── docker-compose.yml  # PostgreSQL + pgvector
├── alembic.ini         # Alembic 配置
├── .env.example        # 环境变量模板
└── pyproject.toml
```

**Schema 只有一份**：`app/models/` 是唯一事实来源，`alembic/versions/` 是它的版本化投影。
（早期用 `sql/init/*.sql` 手工建表，与 ORM 双轨维护必然漂移，已废除。）

---

## 环境搭建

### 1. 安装依赖

```bash
uv sync          # 自动创建 .venv（Python 3.11）并安装全部依赖
```

若 `uv` 不在 PATH 中，可直接使用绝对路径：`~/.local/bin/uv sync`。

### 2. 配置环境变量

```bash
cp .env.example .env
```

需要填写的关键项：

| 变量 | 说明 |
|---|---|
| `EMBEDDING_BASE_URL` | 默认本机 OMLX：`http://127.0.0.1:28080/v1` |
| `EMBEDDING_API_KEY` | 默认 `omlx`（本机占位 Key） |
| `EMBEDDING_MODEL` | 默认 `bge-m3-mlx-4bit`（1024 维） |
| `RERANK_API_KEY` | Reranker 服务密钥 |
| `LLM_*` | 默认走本机 OMLX（`Ling-3.0-tiny-oQ6e`）；也可切 Ollama 或云 API |

> 本机 OMLX 的 Embedding / LLM 不要求真实 Key；远端 OpenAI-compatible 服务才需要。
> `GET /health` 会检查服务可达性与配置模型是否存在，并直接报告各项能力是否可用。

### 3. 启动数据库并建表

```bash
./scripts/db_init.sh                      # 等价于下面两条命令
#   docker compose up -d
#   uv run alembic upgrade head
docker compose ps                         # 等待 healthy
docker compose exec postgres psql -U postgres -d ai_test_knowledge -c '\dx'   # 确认 vector / pg_trgm 扩展
```

Schema 由 Alembic 管理：新增表或改列时，改 `app/models/` 后执行
`uv run alembic revision --autogenerate -m "描述"` 生成迁移，再 `upgrade head`。
`tests/test_migrations.py` 会检查「ORM 与迁移是否一致」以及「迁移能否真正执行」。

### 4. 本地 LLM（可选）

```bash
# 默认复用本机 OMLX（127.0.0.1:28080），无需再启动 Ollama
# 如切回 Ollama，则先执行：
ollama pull qwen3:8b
```

---

## 开发

### 运行测试

```bash
uv run pytest                       # 全量测试
uv run pytest tests/api -q          # 只跑 API 层
uv run ruff check .                 # 静态检查
```

### 索引与 Embedding 探针

```bash
# 只统计将索引多少实体，不调用外部 API
uv run python scripts/index_entities.py --dry-run

# 默认调用本机 OMLX；按来源重建并写入 pgvector
uv run python scripts/index_entities.py --only testcase,defect

# 给 Phase 5 导入的 document Chunk 补向量（增量，不重建正文）
uv run python scripts/index_entities.py --only document

# 切分策略 / Embedding 模型变更后：强制重算 document 的全部向量
uv run python scripts/index_entities.py --only document --rebuild

# Phase 2.1 语义相似度探针（默认 OMLX）
uv run python scripts/embedding_probe.py

# Phase 3 最小 RAG（需要 PostgreSQL + OMLX Embedding + OMLX LLM）
uv run python scripts/ask.py "登录失败怎么测试？"

# Phase 7 Embedding 对比（只写隔离库；dry-run 不调用模型）
uv run python scripts/embedding_experiment.py --dry-run
```

### 数据接入（Phase 5）

```bash
# CSV / Excel；不传 --type 时按表头自动识别
uv run python scripts/ingest.py table data/cases.csv

# Markdown：每个标题生成一个文档 Chunk
uv run python scripts/ingest.py markdown data/spec.md --doc-id DOC-001

# 普通文本 PDF（不做 OCR / 复杂版面还原）
uv run python scripts/ingest.py pdf data/spec.pdf --doc-id DOC-002
```

详细字段映射、清洗规则与限制见 `docs/PHASE5_INGESTION.md`。

> 导入（table / markdown / pdf）刻意不调用 Embedding：批量导入不与模型服务耦合。
> 文档 Chunk 入库时 `embedding` 为空，必须补跑 `index_entities.py --only document`
> 才能参与向量检索；`/health.metrics.chunks_without_embedding` 可以观察缺口。

> `/api/v1/rag/answer` 是实验性慢接口：默认推理模型可能耗时 11–60 秒，
> 请求超过 `RAG_REQUEST_TIMEOUT`（默认 45 秒）返回 503（`llm_error`）；
> 并发超过 `RAG_MAX_CONCURRENCY` 立即返回 429（`rag_overloaded`，带
> `Retry-After: 1`，退避重试即可）。503 只是**提前放弃等待**：
> 底层 LLM 调用无法被中断，后台守护线程仍会跑到 `LLM_TIMEOUT`
> （默认 30 秒，配置层强制 `LLM_TIMEOUT <= RAG_REQUEST_TIMEOUT`）自行结束；
> 后台任务使用自己的会话，不触碰请求级 Session。暂不建议用于交互式生产场景。

`IndexService` 不是 HTTP 路由：索引是批量运维任务，交由脚本执行；
`/api/v1/chunks` 只负责保存 / 查询检索单元，不在请求内生成向量。

**测试不依赖 Docker**：默认用 SQLite 内存库（通过 `app/models/types.py` 的方言分流），
因此任何机器上都能跑 TDD。要针对真实 PostgreSQL 验证时，指向专用测试库即可：

```bash
TEST_DATABASE_URL='postgresql+psycopg://postgres:postgres@127.0.0.1:5432/aitkp_test' uv run pytest
```

> 注意：`TEST_DATABASE_URL` 指向的库会被 `drop_all` + `create_all`，请使用专用测试库。

### 启动服务

```bash
uv run uvicorn app.main:app --reload
# 交互式文档：http://127.0.0.1:8000/docs
```

---

## 当前状态

Phase 0（项目边界）、Phase 1（FastAPI + PostgreSQL）、Phase 2（Chunk + Embedding +
pgvector）、Phase 3（手写最小 RAG）、Phase 4（知识模型收口）、Phase 5（数据接入）、
Phase 6（Chunking 对比实验）与 Phase 7（Embedding 对比）已完成。Phase 6 / 7
报告分别见 [`docs/CHUNKING_EXPERIMENT.md`](docs/CHUNKING_EXPERIMENT.md)、
[`docs/EMBEDDING_EXPERIMENT.md`](docs/EMBEDDING_EXPERIMENT.md)。默认 SQLite 路径
**355 passed + 7 skipped**，真实 PostgreSQL **361 passed + 1 skipped**，全程 TDD
（先红后绿）。测试数随用例增长，以本地实际输出为准。

- [x] 目录骨架（对齐方案 §14）
- [x] 依赖与虚拟环境（uv + Python 3.11）
- [x] 配置层：`app/core/config.py`（pydantic-settings）
- [x] 数据访问层：`app/core/db.py`、`app/core/logging.py`
- [x] ORM 实体：`test_cases` / `defects` / `requirements` / `documents`
- [x] Pydantic Schema：受控词表（Priority / CaseType / Severity / Status）与规范化
- [x] 服务层：通用 CRUD 基类 + 四实体服务 + 统一检索（元数据过滤 + 关键词）
- [x] API：`/health`、`/api/v1/testcases`、`/api/v1/defects`、`/api/v1/requirements`、
      `/api/v1/documents`、`/api/v1/search`
- [x] 统一错误信封 + `X-Request-ID` / `X-Process-Time-Ms` 中间件
- [x] Alembic 初始迁移（含 pgvector / pg_trgm 扩展）+ 漂移检查
- [x] **PostgreSQL 17.11 实机验证**（`vector 0.8.7` / `pg_trgm 1.6`）
- [x] Phase 0：`PROJECT_SCOPE.md` / `LEARNING_QUESTIONS.md`
- [x] Phase 2：`knowledge_chunks`、Embedding 抽象、pgvector 向量检索
- [x] Phase 4：TestCase / Defect / KnowledgeChunk 契约与 Metadata 映射收口
  （见 `docs/PHASE4_KNOWLEDGE_MODEL.md`）
- [x] 索引入口：`uv run python scripts/index_entities.py --dry-run`
- [x] 真实 Embedding 探针：`uv run python scripts/embedding_probe.py`（默认 OMLX）
- [x] Phase 5 数据接入：CSV / Excel / Markdown / PDF + `scripts/ingest.py`
- [x] Phase 6 Chunking 实验：固定窗口 / 语义 / 实体级对比 + Gold Set 指标
  （见 `docs/CHUNKING_EXPERIMENT.md`）
- [x] Phase 7 Embedding 实验：固定 entity+heading，对比 1024/768/384 维模型
  （见 `docs/EMBEDDING_EXPERIMENT.md`）
- [x] document Chunk 补向量入口：`scripts/index_entities.py --only document`
- [ ] 将 Gold Set 扩大到约 100 条人工确认 Query 后复验 Recall@K / MRR
- [x] Phase 3：手写最小 RAG（Vector Search → Context → LLM → Answer + Citation）
  - `POST /api/v1/rag/answer` 与 `scripts/ask.py`
  - 无证据时明确拒答，不调用 LLM；回答只能引用本次检索编号
  - 真实链路已验证：本机 OMLX `Ling-3.0-tiny-oQ6e` + PostgreSQL pgvector
    （见 `docs/RAG_EXPERIMENT.md`）

### 已知取舍（需要时 revisit）

| 事项 | 当前选择 | 原因 |
|---|---|---|
| 中文全文检索 | 关键词检索走 `ILIKE`（全表扫描） | PG 内置 `to_tsvector` 对中文无效；Phase 8 再定 zhparser / pg_jieba / 应用层分词 |
| 检索结果打分 | 不返回 `score` | 没有打分依据时不编数字（Evidence First）；Phase 9 融合后引入 |
| 向量索引 | HNSW + cosine distance | 先验证召回链路；数据量增大后再对比 HNSW / IVFFlat 参数 |
| 检索参数 | TopK / Fusion 配置暂未消费 | 在 Phase 9 / 11 接入 Hybrid / Rerank 时生效 |
| Embedding 语义 | Fake 只验证链路 | 真实召回质量需 bge-m3 + 人工 Gold Set |
| Embedding 选型 | 默认 `bge-m3-mlx-4bit`（1024 维） | Phase 7 小样本 MRR 最高；`embeddinggemma` 接近且更快，待更大 Gold Set 复验 |
| Embedding 实验索引 | 模型独立表 + 精确距离扫描，未建 HNSW | 先隔离模型变量；46 个 Chunk 上 ANN 参数没有可解释性 |
| RAG 检索 | Phase 3 仅 Vector Search TopK | Keyword / Hybrid / Rerank 在 Phase 8–11 接入 |
| RAG 拒答 | 检索为空即拒答；未引用回答统一降级；可选 `max_distance` | 阈值需真实评测集校准，默认不伪造阈值 |
| RAG 延迟 | 实验性同步接口 + 45 秒预算（LLM 30 秒）| 默认推理模型较慢，Phase 14/20 再做性能与异步化 |
| Citation 信号 | `citation_valid` 只表示编号合法 | 不代表 Faithfulness / Citation Accuracy，Phase 14 单独评测 |
| Chunk 索引状态 | `GET /chunks?has_embedding=false` + `/health.metrics` | 无向量 Chunk 不会参与向量检索，必须可排查 |
| CI 范围 | SQLite + ruff | PostgreSQL 门禁仍为手工执行 |
| 文档导入不生成向量 | Phase 5 导入只写 Chunk，`embedding` 为空 | 批量导入不与模型服务耦合；由 `index_entities.py --only document` 增量补齐 |
| Chunking 结论 | 当前只在 3 篇模拟文档、40 条 Query 上验证 | 语料太小，semantic / entity+heading 的优势需在更大 Gold Set 上复验 |
| 不可回答阈值 | 实验记录 top1 distance，但未设置统一 `max_distance` | 6 条负样本不足以校准生产阈值，避免用单点数字制造确定性 |
| RAG 超时语义 | 到点返回 503，但底层 LLM 调用无法中断 | 守护线程任务不能被安全 kill；`LLM_TIMEOUT <= RAG_REQUEST_TIMEOUT` 约束占用上限，Phase 14/20 做异步化 |
| `/models` 健康探测 | 依赖上游实现 `GET /models` 且模型名精确匹配 | 部分 OpenAI-compatible 服务不实现该端点或返回别名，会误报不可用（探测失败会写 warning 日志） |
| Rerank 能力位 | `capabilities.rerank` 只表示已配置 Key | Rerank 在 Phase 11 才接入，未做真实可用性探测 |
| 数组过滤 | `tags` / `related_cases` 暂不支持查询过滤 | PG 的 `text[] @>` 与 SQLite 不可兼得；Phase 10 元数据过滤时补 |
| 业务编号 | API 不可修改（PATCH 传了会 422） | Case ID / Bug ID 是引用出处（Citation）的锚点 |
