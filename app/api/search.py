"""统一检索 API（Phase 1 最小版）。

**这是未来 Hybrid Retrieval 的 HTTP 接缝**：契约（``SearchResponse``）保持稳定，
内部从「元数据过滤 + ILIKE」换成 Vector + Keyword + Fusion + Rerank 之后，
调用方与评测代码都不用改。

注意：当前不返回 score。没有打分依据时给出相似度数字等于伪造证据（Evidence First），
``matched_on`` 用来说明每条结果为什么被召回。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import PaginationDep, SessionDep
from app.models.enums import CaseType, Priority
from app.schemas.search import SearchResponse, SourceType
from app.services.search_service import UnifiedSearchService

router = APIRouter(tags=["search"])


@router.get("/search", response_model=SearchResponse, summary="统一知识检索")
def search(
    pagination: PaginationDep,
    session: SessionDep,
    q: str | None = Query(default=None, description="关键词，匹配标题与正文"),
    source_type: Annotated[
        list[SourceType] | None,
        Query(description="限定检索来源，可重复传入；不传则全部"),
    ] = None,
    module: str | None = None,
    version: str | None = None,
    priority: Priority | None = None,
    status: str | None = Query(
        default=None, description="状态，取规范值如 open / fixed / approved"
    ),
    case_type: Annotated[CaseType | None, Query(alias="type")] = None,
) -> SearchResponse:
    filters = {
        "module": module,
        "version": version,
        "priority": priority.value if priority else None,
        "status": status,
        "case_type": case_type.value if case_type else None,
    }
    effective = {key: value for key, value in filters.items() if value is not None}

    hits, total = UnifiedSearchService.search(
        session,
        q=q,
        source_types=source_type,
        filters=filters,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return SearchResponse(
        query=q,
        filters=effective,
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
        items=hits,
    )


__all__ = ["router"]
