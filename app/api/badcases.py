"""Badcase 闭环 API（Phase 19 持久化）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status
from pydantic import Field

from app.api.deps import SessionDep
from app.core.exceptions import DomainError
from app.evaluation.badcase import Badcase, BadcaseStage, BadcaseStatus
from app.schemas.common import BaseSchema
from app.services.badcase_service import BadcaseService

router = APIRouter(prefix="/badcases", tags=["badcases"])


class BadcaseCreate(BaseSchema):
    """创建 Badcase 的请求体，字段语义与 Phase 19 契约一致。"""

    badcase_id: str = Field(min_length=1, max_length=64)
    query_id: str = Field(min_length=1, max_length=128)
    stage: BadcaseStage
    symptom: str = Field(min_length=1)
    root_cause: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    regression_query_ids: list[str] = Field(min_length=1)
    notes: str | None = None


class BadcaseRead(BaseSchema):
    """Badcase 读取视图。"""

    badcase_id: str
    query_id: str
    stage: BadcaseStage
    symptom: str
    root_cause: str
    hypothesis: str
    regression_query_ids: list[str]
    status: BadcaseStatus
    evaluation_result: dict[str, Any] | None = None
    notes: str | None = None


class BadcaseAdvance(BaseSchema):
    """推进闭环状态的请求体；verified 必须提供评测结果。"""

    evaluation_result: dict[str, Any] | None = None


def _to_read(badcase: Badcase) -> BadcaseRead:
    return BadcaseRead(
        badcase_id=badcase.badcase_id,
        query_id=badcase.query_id,
        stage=badcase.stage,
        symptom=badcase.symptom,
        root_cause=badcase.root_cause,
        hypothesis=badcase.hypothesis,
        regression_query_ids=badcase.regression_query_ids,
        status=badcase.status,
        evaluation_result=badcase.evaluation_result,
        notes=badcase.notes,
    )


@router.post(
    "",
    response_model=BadcaseRead,
    status_code=status.HTTP_201_CREATED,
    summary="登记 Badcase",
)
def create_badcase(payload: BadcaseCreate, session: SessionDep) -> BadcaseRead:
    badcase = Badcase(
        badcase_id=payload.badcase_id,
        query_id=payload.query_id,
        stage=payload.stage,
        symptom=payload.symptom,
        root_cause=payload.root_cause,
        hypothesis=payload.hypothesis,
        regression_query_ids=payload.regression_query_ids,
        notes=payload.notes,
    )
    return _to_read(BadcaseService.create(session, badcase))


@router.get("", response_model=list[BadcaseRead], summary="Badcase 列表（可按阶段 / 状态过滤）")
def list_badcases(
    session: SessionDep,
    stage: BadcaseStage | None = None,
    status_filter: BadcaseStatus | None = None,
) -> list[BadcaseRead]:
    items = BadcaseService.list(session, stage=stage, status=status_filter)
    return [_to_read(item) for item in items]


@router.get("/{badcase_id}", response_model=BadcaseRead, summary="Badcase 详情")
def get_badcase(badcase_id: str, session: SessionDep) -> BadcaseRead:
    return _to_read(BadcaseService.get(session, badcase_id))


@router.post(
    "/{badcase_id}/advance",
    response_model=BadcaseRead,
    summary="推进闭环状态（open → fixing → fixed → verified）",
)
def advance_badcase(
    badcase_id: str,
    session: SessionDep,
    payload: BadcaseAdvance | None = None,
) -> BadcaseRead:
    badcase = BadcaseService.get(session, badcase_id)
    try:
        badcase.advance(evaluation_result=payload.evaluation_result if payload else None)
    except ValueError as exc:
        raise DomainError(str(exc)) from exc
    return _to_read(BadcaseService.save(session, badcase))
