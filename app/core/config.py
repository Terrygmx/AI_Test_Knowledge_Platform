"""全局配置。

所有连接参数与运行参数统一从环境变量 / `.env` 加载，代码中不出现硬编码密钥。
新增配置项时在此声明默认值，并同步更新 `.env.example`。
"""

from functools import lru_cache
from typing import Literal

from pydantic import computed_field
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
    embedding_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_api_key: str = ""
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024
    embedding_batch_size: int = 32

    # ---------------- Reranker（OpenAI-compatible）----------------
    rerank_base_url: str = "https://api.siliconflow.cn/v1"
    rerank_api_key: str = ""
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_top_n: int = 5

    # ---------------- LLM（双通道可切换）----------------
    llm_provider: Literal["ollama", "openai_compatible"] = "ollama"
    llm_base_url: str = "http://127.0.0.1:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "qwen3:8b"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 2048
    llm_timeout: int = 120

    # ---------------- 检索参数 ----------------
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


@lru_cache
def get_settings() -> Settings:
    """进程内单例，避免重复解析 .env。"""
    return Settings()


settings = get_settings()
