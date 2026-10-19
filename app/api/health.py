"""健康检查。

除了探活，还报告各 AI 能力的可用性：未配置 Key 时 Embedding / Rerank
代码路径不可运行，提前暴露比等到调用时才报错更友好。
"""

from __future__ import annotations

import time
from typing import Literal

import httpx
from fastapi import APIRouter
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import __version__
from app.api.deps import SessionDep
from app.core.config import settings
from app.core.logging import get_logger
from app.schemas.common import BaseSchema
from app.services.knowledge_chunk_service import KnowledgeChunkService

logger = get_logger(__name__)

router = APIRouter(tags=["health"])

_llm_cache: tuple[float, bool] | None = None
_LLM_CACHE_TTL_SECONDS = 60.0


class HealthResponse(BaseSchema):
    """健康检查响应。"""

    status: Literal["ok", "degraded"]
    app: str
    version: str
    environment: str
    checks: dict[str, bool]
    metrics: dict[str, int] = Field(default_factory=dict)
    capabilities: dict[str, bool]


@router.get("/health", response_model=HealthResponse, summary="健康检查与能力自检")
def health(session: SessionDep) -> HealthResponse:
    database = _check_database(session)
    chunks_without_embedding = (
        KnowledgeChunkService.count_without_embedding(session) if database else None
    )
    llm_ready = _check_llm()
    capabilities = {
        # 本地 Ollama 不需要 Key；云 API 通道需要
        "embedding": bool(settings.embedding_api_key),
        "rerank": bool(settings.rerank_api_key),
        "llm": llm_ready,
        # 向量检索依赖 PostgreSQL + pgvector；SQLite 默认测试路径不支持
        "vector_search": _supports_vector_search(session),
    }
    return HealthResponse(
        status="ok" if database else "degraded",
        app=settings.app_name,
        version=__version__,
        environment=settings.app_env,
        checks={"database": database},
        metrics=(
            {"chunks_without_embedding": chunks_without_embedding}
            if chunks_without_embedding is not None
            else {}
        ),
        capabilities=capabilities,
    )


def _check_database(session: SessionDep) -> bool:
    try:
        session.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError as exc:
        logger.warning("数据库健康检查失败：%s", exc)
        session.rollback()
        return False


def _supports_vector_search(session: SessionDep) -> bool:
    return session.bind is not None and session.bind.dialect.name == "postgresql"


def _check_llm() -> bool:
    """探测 LLM 通道；结果短时缓存，避免每次 /health 都等待上游。"""
    global _llm_cache
    now = time.monotonic()
    if _llm_cache is not None and now - _llm_cache[0] < _LLM_CACHE_TTL_SECONDS:
        return _llm_cache[1]
    if settings.llm_provider == "openai_compatible":
        ready = bool(settings.llm_api_key)
        _llm_cache = (now, ready)
        return ready

    try:
        base = settings.llm_base_url.rstrip("/")
        # Ollama 的 OpenAI-compatible base 为 /v1，原生标签接口在根路径。
        root = base.removesuffix("/v1")
        response = httpx.get(f"{root}/api/tags", timeout=1.5)
        ready = response.status_code == 200
    except Exception:
        ready = False
    _llm_cache = (now, ready)
    return ready


__all__ = ["HealthResponse", "router"]
