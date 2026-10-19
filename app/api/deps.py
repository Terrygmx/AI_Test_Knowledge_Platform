"""公共依赖：数据库会话、分页参数。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.schemas.common import Pagination

#: 请求级 SQLAlchemy 会话（由 app.core.db.get_session 提供）
SessionDep = Annotated[Session, Depends(get_session)]


def _pagination(
    limit: int = Query(20, ge=1, le=100, description="每页条数"),
    offset: int = Query(0, ge=0, description="偏移量"),
) -> Pagination:
    return Pagination(limit=limit, offset=offset)


#: 分页查询参数
PaginationDep = Annotated[Pagination, Depends(_pagination)]


__all__ = ["PaginationDep", "SessionDep"]
