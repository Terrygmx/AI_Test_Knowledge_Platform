"""实体服务基类：把 CRUD 样板集中在一处。

子类只声明差异（``model`` / ``unique_field`` / ``equality_filters`` / ``search_fields``），
读写逻辑完全复用。事务边界：**写操作在本层 commit**，失败即 rollback，
因此调用方（路由 / 脚本）不需要关心会话生命周期。

Phase 1 阶段的检索能力仅限「元数据等值过滤 + 关键词 ILIKE」，
向量 / BM25 / 融合 / Rerank 在 Phase 3/8/9/11 接入 ``app/retrieval`` 后替换。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Generic, TypeVar

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import DuplicateError, NotFoundError
from app.core.logging import get_logger

logger = get_logger(__name__)

M = TypeVar("M")


class EntityService(Generic[M]):
    """通用实体服务。"""

    model: type[M]
    #: 人类可读名称，用于错误信息
    label: str = "记录"
    #: 业务唯一字段（Case ID / Bug ID / Req ID / Doc ID）
    unique_field: str = "id"
    #: 支持的等值过滤字段（查询参数名 == 列名）
    equality_filters: tuple[str, ...] = ()
    #: 关键词检索覆盖的文本字段
    search_fields: tuple[str, ...] = ()

    # ------------------------------------------------------------------ 读

    @classmethod
    def get(cls, session: Session, pk: int) -> M | None:
        """按主键读取。"""
        return session.get(cls.model, pk)

    @classmethod
    def get_by_unique(cls, session: Session, value: str) -> M | None:
        """按业务编号读取（公开 API 统一用业务编号，用户引用的是 Case ID / Bug ID）。"""
        stmt = select(cls.model).where(getattr(cls.model, cls.unique_field) == value)
        return session.scalars(stmt).first()

    @classmethod
    def get_by_unique_or_404(cls, session: Session, value: str) -> M:
        obj = cls.get_by_unique(session, value)
        if obj is None:
            raise NotFoundError(f"{cls.label}不存在：{cls.unique_field}={value}")
        return obj

    @classmethod
    def list(
        cls,
        session: Session,
        *,
        offset: int = 0,
        limit: int = 20,
        filters: Mapping[str, Any] | None = None,
        q: str | None = None,
    ) -> tuple[list[M], int]:
        """分页 + 元数据过滤 + 关键词检索，返回 ``(items, total)``。"""
        stmt = cls._build_query(filters, q)
        total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        stmt = stmt.order_by(cls.model.id).limit(limit).offset(offset)  # type: ignore[attr-defined]
        return list(session.scalars(stmt).all()), total

    @classmethod
    def count(
        cls,
        session: Session,
        *,
        filters: Mapping[str, Any] | None = None,
        q: str | None = None,
    ) -> int:
        """只计数不取行（跨实体检索时用来算 total）。"""
        stmt = select(func.count()).select_from(cls._build_query(filters, q).subquery())
        return session.scalar(stmt) or 0

    @classmethod
    def _build_query(cls, filters: Mapping[str, Any] | None, q: str | None) -> Select:
        """过滤 + 关键词检索的公共 SQL 构造。"""
        stmt = select(cls.model)
        for key, value in (filters or {}).items():
            if value is None or key not in cls.equality_filters:
                continue
            stmt = stmt.where(getattr(cls.model, key) == value)
        return cls._apply_search(stmt, q)

    @classmethod
    def _apply_search(cls, stmt: Select, q: str | None) -> Select:
        """关键词检索：对 ``search_fields`` 做大小写不敏感的子串匹配。"""
        if not q or not cls.search_fields:
            return stmt
        pattern = f"%{q.strip()}%"
        conditions = [getattr(cls.model, field).ilike(pattern) for field in cls.search_fields]
        return stmt.where(or_(*conditions))

    # ------------------------------------------------------------------ 写

    @classmethod
    def create(cls, session: Session, *, data: Mapping[str, Any]) -> M:
        """新增。业务编号重复时抛 ``DuplicateError``（409）。"""
        cls._ensure_unique(session, data)
        obj = cls.model(**dict(data))
        session.add(obj)
        cls._commit(session, value=data.get(cls.unique_field))
        return obj

    @classmethod
    def update(cls, session: Session, obj: M, *, data: Mapping[str, Any]) -> M:
        """局部更新：只写 ``data`` 里出现的字段。"""
        cls._ensure_unique(session, data, exclude_id=getattr(obj, "id", None))
        for key, value in data.items():
            setattr(obj, key, value)
        cls._commit(session, value=data.get(cls.unique_field))
        return obj

    @classmethod
    def delete(cls, session: Session, obj: M) -> None:
        session.delete(obj)
        cls._commit(session)

    @classmethod
    def _commit(cls, session: Session, *, value: str | None = None) -> None:
        """flush + commit。

        预检之外的最后防线：并发写入撞上唯一约束时，回滚并把 ``IntegrityError``
        转成业务错误（409）。不回滚的话会话会停留在 broken 状态，后续操作全废。
        """
        try:
            session.flush()
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            logger.warning("%s 写入冲突（%s）：%s", cls.label, value, exc)
            raise DuplicateError(
                f"{cls.label} {value} 已存在" if value else f"{cls.label} 写入冲突",
                detail={"field": cls.unique_field, "value": value},
            ) from exc

    @classmethod
    def _ensure_unique(
        cls, session: Session, data: Mapping[str, Any], *, exclude_id: int | None = None
    ) -> None:
        """写前预检唯一性：给出可读的错误信息，而不是把 IntegrityError 透传给 500。"""
        if cls.unique_field not in data:
            return
        value = data[cls.unique_field]
        stmt = select(cls.model.id).where(getattr(cls.model, cls.unique_field) == value)  # type: ignore[attr-defined]
        if exclude_id is not None:
            stmt = stmt.where(cls.model.id != exclude_id)  # type: ignore[attr-defined]
        if session.scalars(stmt).first() is not None:
            raise DuplicateError(
                f"{cls.label} {value} 已存在",
                detail={"field": cls.unique_field, "value": value},
            )


__all__ = ["EntityService"]
