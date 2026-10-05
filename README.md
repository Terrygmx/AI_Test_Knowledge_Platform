# AI Test Knowledge Platform

面向测试工程的知识智能平台：统一接入测试用例、缺陷、需求/产品文档与测试规范，
通过结构化知识建模 + Hybrid Retrieval（Vector + Keyword + Metadata Filter）+ Rerank + RAG，
为测试人员以及后续的智能回归测试 Agent 提供**可追溯**的知识检索与问答能力。

> **Retrieval First** —— 先证明检索正确，再做生成。
> **Evidence First** —— 回答必须有来源，不允许凭空生成 Case ID / Bug ID。

详细方案见 [`docs/AI_Test_Knowledge_Platform_V2.md`](docs/AI_Test_Knowledge_Platform_V2.md)，
开发计划见 [`docs/AI_Test_Knowledge_Platform_Implementation_Plan.md`](docs/AI_Test_Knowledge_Platform_Implementation_Plan.md)。

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
| LLM | OpenAI-compatible，双通道：本地 Ollama / 云 API，`.env` 一键切换 |
| 评测 | 自研指标（Recall@K / Precision@K / MRR / NDCG）+ 后续接 Ragas |
| 工程 | uv + pytest + ruff |

**V1 不引入 Milvus。** 测试数据具有强结构化特征，PostgreSQL 单库即可同时承担关系数据、
Metadata 过滤、全文检索与向量检索，避免多套存储带来的数据一致性问题。

---

## 目录结构

```
AI_Test_Knowledge_Platform/
├── app/
│   ├── core/           # 基础设施：配置、数据库会话、日志
│   ├── api/            # FastAPI 路由层
│   ├── ingestion/      # 数据接入：Parser → Normalizer → Knowledge Model → Chunker
│   ├── retrieval/      # 检索：Vector / Keyword / Hybrid Fusion / Metadata Filter
│   ├── rerank/         # 重排
│   ├── generation/     # 上下文组装 + LLM + Citation
│   ├── evaluation/     # 离线评测指标与运行器
│   └── models/         # 数据模型（ORM / Pydantic Schema）
├── docs/               # 项目方案、开发计划、各阶段实验报告
├── eval/               # 评测数据集（约 100 条人工确认 Query）
├── scripts/            # 数据导入、索引、实验脚本
├── sql/init/           # 数据库初始化 DDL
├── tests/              # 测试代码
├── data/               # 原始语料与产物（不入库）
├── docker-compose.yml  # PostgreSQL + pgvector
├── .env.example        # 环境变量模板
└── pyproject.toml
```

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
| `EMBEDDING_API_KEY` | Embedding 服务密钥（默认指向硅基流动 bge-m3） |
| `RERANK_API_KEY` | Reranker 服务密钥 |
| `LLM_*` | 默认走本地 Ollama（`qwen3:8b`）；切云 API 时改 `LLM_PROVIDER=openai_compatible` 并填 Key |

> 未配置 Key 时，涉及 Embedding / Rerank / LLM 的代码路径不可运行，但项目可正常导入与启动。

### 3. 启动数据库（当前待办）

本机尚未安装 Docker / PostgreSQL，`docker-compose.yml` 与 `sql/init/01_schema.sql` 已备好待用。

```bash
# 安装 Docker Desktop 后：
docker compose up -d
docker compose ps                 # 等待 healthy
docker compose exec postgres psql -U postgres -d ai_test_knowledge -c '\dx'   # 确认 vector 扩展
```

首次启动会自动执行 `sql/init/*.sql` 完成建表。

### 4. 本地 LLM（可选）

```bash
ollama pull qwen3:8b     # 本机 Ollama 服务已在运行（127.0.0.1:11434），但尚无任何模型
```

---

## 当前状态

- [x] 目录骨架（对齐方案 §14）
- [x] 依赖与虚拟环境（uv + Python 3.11）
- [x] 配置层：`app/core/config.py`（pydantic-settings）
- [x] 数据访问层骨架：`app/core/db.py`、`app/core/logging.py`
- [x] `docker-compose.yml` + `sql/init/01_schema.sql`（初稿）
- [ ] **数据库实机验证**（待安装 Docker / PostgreSQL）
- [ ] 项目方案与开发计划评审（`docs/`）
- [ ] Phase 0：`PROJECT_SCOPE.md` / `LEARNING_QUESTIONS.md`
- [ ] Phase 1：FastAPI + PostgreSQL

> `sql/init/01_schema.sql` 是**初稿**。方案要求 Schema 由本人定稿，
> 请在 Phase 1 评审字段后再固化，并让 Alembic 接管后续变更。
