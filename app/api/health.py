"""健康检查。

除了探活，还探测模型服务与配置的模型是否存在。这样服务未启动、模型名写错
或远端 Key 缺失都会提前暴露，而不是等到第一次 RAG 请求才报错。
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
_embedding_cache: tuple[float, bool] | None = None
_MODEL_CACHE_TTL_SECONDS = 60.0


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
    embedding_ready = _check_embedding()
    capabilities = {
        # 本机 OpenAI-compatible 服务不需要 Key，但仍需服务与模型真实可用
        "embedding": embedding_ready,
        # Rerank 在 Phase 11 才接入，这里只表示“已配置 Key”，不是真实可用性探测；
        # 与 llm / embedding 的口径不同，接入时统一改为 _model_available 探测
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
    if _llm_cache is not None and now - _llm_cache[0] < _MODEL_CACHE_TTL_SECONDS:
        return _llm_cache[1]
    if settings.llm_provider == "openai_compatible":
        ready = bool(settings.llm_api_key) and _model_available(
            settings.llm_base_url,
            settings.llm_api_key,
            settings.llm_model,
        )
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


def _check_embedding() -> bool:
    """确认 Embedding 服务可达且配置模型存在；本机服务同样需要真实探测。"""
    global _embedding_cache
    now = time.monotonic()
    if _embedding_cache is not None and now - _embedding_cache[0] < _MODEL_CACHE_TTL_SECONDS:
        return _embedding_cache[1]
    ready = _model_available(
        settings.embedding_base_url,
        settings.embedding_api_key,
        settings.embedding_model,
    )
    _embedding_cache = (now, ready)
    return ready


def _model_available(base_url: str, api_key: str, model: str) -> bool:
    """检查 ``GET /models`` 是否返回目标模型。"""
    # 前提：上游是 OpenAI-compatible 且实现了 ``GET /models``。部分兼容服务不实现该端点
    # 或返回带后缀的别名，此时会误报不可用（见 README 已知取舍）。失败必须写 warning，
    # 不能静默吞掉：否则「模型不可用」只能靠猜。
    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key or 'local-no-key'}"},
            timeout=1.5,
        )
        if response.status_code != 200:
            logger.warning("模型探测失败 base_url=%s status=%s", base_url, response.status_code)
            return False
        payload = response.json()
        models = payload.get("data", [])
        available = any(item.get("id") == model for item in models if isinstance(item, dict))
        if not available:
            logger.warning("模型探测未命中 base_url=%s model=%s", base_url, model)
        return available
    except Exception as exc:
        logger.warning("模型探测异常 base_url=%s error=%s", base_url, exc)
        return False


__all__ = ["HealthResponse", "router"]
