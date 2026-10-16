"""健康检查。

除了探活，还报告各 AI 能力的可用性：未配置 Key 时 Embedding / Rerank
代码路径不可运行，提前暴露比等到调用时才报错更友好。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import __version__
from app.api.deps import SessionDep
from app.core.config import settings
from app.core.logging import get_logger
from app.schemas.common import BaseSchema

logger = get_logger(__name__)

router = APIRouter(tags=["health"])


class HealthResponse(BaseSchema):
    """健康检查响应。"""

    status: Literal["ok", "degraded"]
    app: str
    version: str
    environment: str
    checks: dict[str, bool]
    capabilities: dict[str, bool]


@router.get("/health", response_model=HealthResponse, summary="健康检查与能力自检")
def health(session: SessionDep) -> HealthResponse:
    database = _check_database(session)
    capabilities = {
        # 本地 Ollama 不需要 Key；云 API 通道需要
        "embedding": bool(settings.embedding_api_key),
        "rerank": bool(settings.rerank_api_key),
        "llm": True,
    }
    return HealthResponse(
        status="ok" if database else "degraded",
        app=settings.app_name,
        version=__version__,
        environment=settings.app_env,
        checks={"database": database},
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


__all__ = ["HealthResponse", "router"]
