"""全局配置。

所有连接参数与运行参数统一从环境变量 / `.env` 加载，代码中不出现硬编码密钥。
新增配置项时在此声明默认值，并同步更新 `.env.example`。
"""

from functools import lru_cache
from typing import Literal, Self

from pydantic import computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """项目配置，字段名与环境变量名大小写不敏感地一一对应。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------- 应用 ----------------
    app_name: str = "AI Test Knowledge Platform"
    app_env: str = "local"
    debug: bool = True
    log_level: str = "INFO"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_prefix: str = "/api/v1"

    # ---------------- PostgreSQL + pgvector ----------------
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_db: str = "ai_test_knowledge"
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"

    # ---------------- Embedding（OpenAI-compatible）----------------
    embedding_base_url: str = "http://127.0.0.1:28080/v1"
    embedding_api_key: str = ""
    embedding_model: str = "bge-m3-mlx-4bit"
    embedding_dim: int = 1024
    embedding_batch_size: int = 32

    # ---------------- Reranker（OpenAI-compatible）----------------
    rerank_base_url: str = "https://api.siliconflow.cn/v1"
    rerank_api_key: str = ""
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_top_n: int = 5

    # ---------------- LLM（双通道可切换）----------------
    llm_provider: Literal["ollama", "openai_compatible"] = "openai_compatible"
    llm_base_url: str = "http://127.0.0.1:28080/v1"
    llm_api_key: str = "omlx"
    llm_model: str = "Ling-3.0-tiny-oQ6e"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 2048
    # 必须 <= rag_request_timeout：否则「到点返回 503」之后底层调用还能继续占用资源。
    # 30 秒来自实测：本机 OMLX Ling-3.0-tiny-oQ6e 单次生成约 30 秒（含推理过程输出），
    # 再调小会先把真实请求掐断，而不是保护服务。
    llm_timeout: int = 30
    #: RAG 请求的端到端预算（含 Embedding + 检索 + LLM）；超时返回 503
    rag_request_timeout: float = 45.0
    #: 同时执行的 RAG 推理任务上限；超出的请求直接返回 429，避免线程与连接被拖垮
    rag_max_concurrency: int = 4

    # ---------------- 检索参数（Phase 9/11 起被 Hybrid / Rerank 消费）----------------
    retrieval_vector_top_k: int = 20
    retrieval_keyword_top_k: int = 20
    retrieval_fusion: Literal["rrf", "weighted"] = "rrf"
    retrieval_rrf_k: int = 60
    retrieval_final_top_k: int = 5

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        """SQLAlchemy 连接串（psycopg3 驱动）。"""
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @model_validator(mode="after")
    def _validate_timeout_budget(self) -> Self:
        """端到端预算必须不大于底层超时，否则「30 秒预算」是无效承诺。

        关系颠倒时（``llm_timeout`` > ``rag_request_timeout``）接口会先返回 503，
        但底层调用还能继续占用资源数十秒；在配置层 fail fast，避免误判超时生效。
        """
        if float(self.llm_timeout) > float(self.rag_request_timeout):
            raise ValueError(
                "LLM_TIMEOUT 必须 <= RAG_REQUEST_TIMEOUT，"
                f"当前 llm_timeout={self.llm_timeout}, rag_request_timeout={self.rag_request_timeout}"
            )
        if self.rag_max_concurrency < 1:
            raise ValueError("RAG_MAX_CONCURRENCY 必须 >= 1")
        return self


@lru_cache
def get_settings() -> Settings:
    """进程内单例，避免重复解析 .env。"""
    return Settings()


settings = get_settings()
