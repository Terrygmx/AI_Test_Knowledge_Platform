"""FastAPI 应用工厂。

部署形态：``uvicorn app.main:app``。测试里通过 ``create_app()`` 拿新实例，
以便覆盖依赖（换测试数据库、注册临时路由）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api import api_router, health_router
from app.api.errors import register_exception_handlers
from app.api.middleware import RequestContextMiddleware
from app.core.config import settings
from app.core.db import engine
from app.core.logging import get_logger, setup_logging

logger = get_logger(__name__)

OPENAPI_TAGS = [
    {"name": "health", "description": "健康检查与能力自检"},
    {"name": "testcases", "description": "测试用例"},
    {"name": "defects", "description": "缺陷"},
    {"name": "requirements", "description": "需求"},
    {"name": "documents", "description": "文档元数据"},
    {"name": "search", "description": "统一知识检索（Phase 1 最小版）"},
]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    logger.info(
        "启动 %s v%s env=%s llm_provider=%s",
        settings.app_name,
        __version__,
        settings.app_env,
        settings.llm_provider,
    )
    yield
    engine.dispose()
    logger.info("已关闭数据库连接池")


def create_app() -> FastAPI:
    """构建应用：中间件 → 异常处理 → 路由。"""
    setup_logging()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=(
            "面向测试工程的知识智能平台：结构化知识建模 + Hybrid Retrieval + Rerank + RAG。\n\n"
            "- Retrieval First：先证明检索正确，再做生成\n"
            "- Evidence First：回答必须有来源，不凭空生成 Case ID / Bug ID\n"
            "- Evaluation First-class：改 Chunk / Embedding / TopK / Rerank 都可重新评测"
        ),
        lifespan=lifespan,
        openapi_tags=OPENAPI_TAGS,
    )

    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    # /health 挂在根路径（探活更直观），业务接口统一走 /api/v1
    app.include_router(health_router)
    app.include_router(api_router)
    return app


app = create_app()

__all__ = ["app", "create_app"]
