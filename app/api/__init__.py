"""API 路由统一出口。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.badcases import router as badcases_router
from app.api.chunks import router as chunks_router
from app.api.defects import router as defects_router
from app.api.health import router as health_router
from app.api.rag import router as rag_router
from app.api.requirements_documents import document_router, requirement_router
from app.api.search import router as search_router
from app.api.testcases import router as testcases_router
from app.core.config import settings

api_router = APIRouter(prefix=settings.api_prefix)
api_router.include_router(badcases_router)
api_router.include_router(testcases_router)
api_router.include_router(defects_router)
api_router.include_router(requirement_router)
api_router.include_router(document_router)
api_router.include_router(chunks_router)
api_router.include_router(search_router)
api_router.include_router(rag_router)

__all__ = ["api_router", "health_router"]
