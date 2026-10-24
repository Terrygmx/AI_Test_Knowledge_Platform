"""Badcase 持久化服务：Phase 19 契约与数据库行之间的双向转换。

闭环语义（状态顺序、verified 必须带评测结果、回归 Query 必填）全部由
``app.evaluation.badcase.Badcase`` 契约层负责；本服务只做存储、唯一性
兜底和查询，不复制业务规则。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import DuplicateError, NotFoundError
from app.evaluation.badcase import Badcase, BadcaseStage, BadcaseStatus
from app.models import EvaluationBadcase


class BadcaseService:
    """Badcase 的写入与查询。"""

    @classmethod
    def to_contract(cls, row: EvaluationBadcase) -> Badcase:
        """数据库行 → Phase 19 契约对象。"""
        return Badcase(
            badcase_id=row.badcase_id,
            query_id=row.query_id,
            stage=BadcaseStage(row.stage),
            symptom=row.symptom,
            root_cause=row.root_cause,
            hypothesis=row.hypothesis,
            regression_query_ids=list(row.regression_query_ids or []),
            status=BadcaseStatus(row.status),
            evaluation_result=row.evaluation_result,
            notes=row.notes,
        )

    @classmethod
    def create(cls, session: Session, badcase: Badcase) -> Badcase:
        """写入一条 Badcase；``badcase_id`` 重复时返回 409 语义。"""
        row = EvaluationBadcase(
            badcase_id=badcase.badcase_id,
            query_id=badcase.query_id,
            stage=badcase.stage.value,
            symptom=badcase.symptom,
            root_cause=badcase.root_cause,
            hypothesis=badcase.hypothesis,
            regression_query_ids=list(badcase.regression_query_ids),
            status=badcase.status.value,
            evaluation_result=badcase.evaluation_result,
            notes=badcase.notes,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError as exc:
            raise DuplicateError(f"badcase_id 已存在：{badcase.badcase_id}") from exc
        return badcase

    @classmethod
    def save(cls, session: Session, badcase: Badcase) -> Badcase:
        """upsert 语义：按 ``badcase_id`` 覆盖业务字段，用于闭环状态推进后的回写。"""
        row = session.execute(
            select(EvaluationBadcase).where(EvaluationBadcase.badcase_id == badcase.badcase_id)
        ).scalar_one_or_none()
        if row is None:
            return cls.create(session, badcase)
        row.query_id = badcase.query_id
        row.stage = badcase.stage.value
        row.symptom = badcase.symptom
        row.root_cause = badcase.root_cause
        row.hypothesis = badcase.hypothesis
        row.regression_query_ids = list(badcase.regression_query_ids)
        row.status = badcase.status.value
        row.evaluation_result = badcase.evaluation_result
        row.notes = badcase.notes
        session.flush()
        return badcase

    @classmethod
    def get(cls, session: Session, badcase_id: str) -> Badcase:
        row = session.execute(
            select(EvaluationBadcase).where(EvaluationBadcase.badcase_id == badcase_id)
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError(f"badcase 不存在：{badcase_id}")
        return cls.to_contract(row)

    @classmethod
    def list(
        cls,
        session: Session,
        *,
        stage: BadcaseStage | None = None,
        status: BadcaseStatus | None = None,
    ) -> list[Badcase]:
        stmt = select(EvaluationBadcase).order_by(EvaluationBadcase.id)
        if stage is not None:
            stmt = stmt.where(EvaluationBadcase.stage == stage.value)
        if status is not None:
            stmt = stmt.where(EvaluationBadcase.status == status.value)
        rows = session.execute(stmt).scalars()
        return [cls.to_contract(row) for row in rows]
