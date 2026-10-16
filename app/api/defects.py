"""缺陷 API。"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import PaginationDep, SessionDep
from app.models import Defect
from app.models.enums import DefectStatus, Priority, Severity
from app.schemas.common import Page
from app.schemas.defect import DefectCreate, DefectRead, DefectUpdate
from app.services.defect_service import DefectService

router = APIRouter(prefix="/defects", tags=["defects"])


@router.post(
    "",
    response_model=DefectRead,
    status_code=status.HTTP_201_CREATED,
    summary="新增缺陷",
)
def create_defect(payload: DefectCreate, session: SessionDep) -> Defect:
    return DefectService.create(session, data=payload.to_orm_dict())


@router.get("", response_model=Page[DefectRead], summary="缺陷列表（元数据过滤 + 关键词）")
def list_defects(
    pagination: PaginationDep,
    session: SessionDep,
    module: str | None = None,
    version: str | None = None,
    severity: Severity | None = None,
    priority: Priority | None = None,
    status: DefectStatus | None = None,
    q: str | None = None,
) -> Page[DefectRead]:
    items, total = DefectService.list(
        session,
        offset=pagination.offset,
        limit=pagination.limit,
        filters={
            "module": module,
            "version": version,
            "severity": severity.value if severity else None,
            "priority": priority.value if priority else None,
            "status": status.value if status else None,
        },
        q=q,
    )
    return Page[DefectRead].build(
        items=items, total=total, limit=pagination.limit, offset=pagination.offset
    )


@router.get("/{bug_id}", response_model=DefectRead, summary="按 Bug ID 查询")
def get_defect(bug_id: str, session: SessionDep) -> Defect:
    return DefectService.get_by_unique_or_404(session, bug_id)


@router.patch("/{bug_id}", response_model=DefectRead, summary="局部更新缺陷")
def update_defect(bug_id: str, payload: DefectUpdate, session: SessionDep) -> Defect:
    defect = DefectService.get_by_unique_or_404(session, bug_id)
    return DefectService.update(session, defect, data=payload.to_orm_dict(exclude_unset=True))


@router.delete("/{bug_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除缺陷")
def delete_defect(bug_id: str, session: SessionDep) -> None:
    defect = DefectService.get_by_unique_or_404(session, bug_id)
    DefectService.delete(session, defect)


__all__ = ["router"]
