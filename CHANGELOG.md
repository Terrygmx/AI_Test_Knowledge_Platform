# Changelog

本项目所有值得记录的变更都写在这里。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased]

### Added
- 项目目录骨架，对齐方案 §14 的 `app/` 布局（`core` / `api` / `ingestion` / `retrieval` / `rerank` / `generation` / `evaluation` / `models`）。
- 依赖声明 `pyproject.toml`：FastAPI、SQLAlchemy、Alembic、psycopg3、pgvector、openai、pandas、pypdf、pytest、ruff 等。
- 虚拟环境：`uv sync` + Python 3.11（`.python-version`）。
- 配置层 `app/core/config.py`：基于 pydantic-settings，统一从 `.env` 读取应用 / 数据库 / Embedding / Rerank / LLM / 检索参数。
- 数据访问骨架 `app/core/db.py`：SQLAlchemy 2.0 engine、session、`session_scope()`、`ping()`。
- 日志骨架 `app/core/logging.py`：统一格式，为后续 Badcase 阶段日志打底。
- `docker-compose.yml`：PostgreSQL 17 + pgvector（`pgvector/pgvector:pg17`），含健康检查与初始化脚本挂载。
- `sql/init/01_schema.sql`（初稿）：业务实体表（test_cases / defects / requirements / documents）、检索表（knowledge_chunks，含 `vector(1024)` 与 HNSW 索引）、评测表（evaluation_cases / evaluation_results / badcases）。
- `.env.example`：Embedding / Rerank / LLM 双通道（Ollama ↔ 云 API）配置模板。
- `.gitignore`：排除 `.venv`、`.env`、`data/`、`.workbuddy-ai/` 等。

### Notes
- **数据库未实机验证**：本机无 Docker / PostgreSQL，`docker-compose.yml` 处于「备好待用」状态。
- **中文全文检索待定**：PostgreSQL 内置 `to_tsvector` 对中文无效，DDL 中 `tsv` 先保留为普通列，待选定 zhparser / pg_trgm / 应用层分词方案后再固化。
- **Embedding 维度耦合**：`knowledge_chunks.embedding` 固定为 `vector(1024)`（bge-m3），更换模型需同步改列与索引。
