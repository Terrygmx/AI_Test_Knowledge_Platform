"""统一检索服务（Phase 1 最小版：元数据过滤 + 关键词 ILIKE）。

**这个模块是未来 Hybrid Retrieval 的接缝。** 明确不做的事：

- 不做向量检索 / BM25 / RRF 融合 / Rerank（Phase 3 / 8 / 9 / 11 引入）；
- **不返回 score**：在没有打分依据时给出相似度数字等于伪造证据，违反 Evidence First。
  排序是确定性的（来源类型 + 业务编号），``matched_on`` 说明每条结果为什么被召回。

Phase 3 起由 ``app/retrieval`` 接管：同一份 ``SearchResponse`` 契约不变，
内部换成 Vector + Keyword + Fusion + Rerank，评测体系（Recall@K / MRR）才能对比。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import DomainError
from app.core.logging import get_logger
from app.schemas.search import SearchHit, SourceType
from app.services.base import EntityService
from app.services.defect_service import DefectService
from app.services.document_service import DocumentService
from app.services.requirement_service import RequirementService
from app.services.testcase_service import TestCaseService

logger = get_logger(__name__)

#: 参与统一检索的实体，顺序即结果分组顺序
ENTITY_SERVICES: tuple[tuple[SourceType, type[EntityService]], ...] = (
    ("testcase", TestCaseService),
    ("defect", DefectService),
    ("requirement", RequirementService),
    ("document", DocumentService),
)

#: snippet 截取宽度（字符）
SNIPPET_WIDTH = 120


class UnifiedSearchService:
    """跨实体检索。"""

    @classmethod
    def search(
        cls,
        session: Session,
        *,
        q: str | None = None,
        source_types: Iterable[SourceType] | None = None,
        filters: Mapping[str, Any] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[SearchHit], int]:
        """执行检索，返回 ``(hits, total)``。

        分页策略：先用 COUNT 得到准确的 ``total``，再按实体顺序跳过 / 取数，
        避免为了排序把所有候选行拉进内存（当前数据量下也足够快）。
        """
        specs = cls._resolve_specs(source_types)
        skip = max(offset, 0)
        # limit <= 0 就是「不要结果」，不要擅自抬成 1
        take = max(limit, 0)
        hits: list[SearchHit] = []
        total = 0

        for source_type, service in specs:
            if not cls._supports_filters(service, filters):
                # 该实体没有 requested 过滤条件对应的列（例如 document 没有 module），
                # 必须整实体排除，而不是「忽略条件后返回全部」——
                # 否则 module=payment 会把没有模块信息的文档也算进结果。
                continue
            entity_filters = {
                key: value for key, value in (filters or {}).items() if value is not None
            }
            count = service.count(session, filters=entity_filters, q=q)
            total += count
            if skip >= count:
                skip -= count
                continue

            fetch = min(take, count - skip)
            if fetch > 0:
                rows, _ = service.list(
                    session, offset=skip, limit=fetch, filters=entity_filters, q=q
                )
                hits.extend(cls._to_hit(source_type, service, row, q) for row in rows)
                take -= len(rows)
            skip = 0
            # 注意：本页取满（take == 0）时不能 break —— total 还要继续统计后续实体。
            # 后续循环里 fetch = min(0, ...) = 0，自然不再取行。

        return hits, total

    # ------------------------------------------------------------------ 内部

    @classmethod
    def _supports_filters(
        cls, service: type[EntityService], filters: Mapping[str, Any] | None
    ) -> bool:
        """该实体是否能满足全部生效的过滤条件。"""
        return all(
            value is None or key in service.equality_filters
            for key, value in (filters or {}).items()
        )

    @classmethod
    def _resolve_specs(
        cls, source_types: Iterable[SourceType] | None
    ) -> list[tuple[SourceType, type[EntityService]]]:
        if not source_types:
            return list(ENTITY_SERVICES)
        wanted = set(source_types)
        known = {source_type for source_type, _ in ENTITY_SERVICES}
        unknown = wanted - known
        if unknown:
            raise DomainError(
                f"未知检索来源：{sorted(unknown)}",
                detail={"allowed": sorted(known)},
            )
        return [(st, svc) for st, svc in ENTITY_SERVICES if st in wanted]

    @classmethod
    def _to_hit(
        cls,
        source_type: SourceType,
        service: type[EntityService],
        obj: Any,
        q: str | None,
    ) -> SearchHit:
        matched_on = cls._matched_fields(service, obj, q)
        return SearchHit(
            source_type=source_type,
            source_id=str(getattr(obj, service.unique_field)),
            title=str(getattr(obj, "title", None) or getattr(obj, service.unique_field)),
            module=getattr(obj, "module", None),
            version=getattr(obj, "version", None),
            priority=getattr(obj, "priority", None),
            severity=getattr(obj, "severity", None),
            status=getattr(obj, "status", None),
            snippet=make_snippet(cls._snippet_text(service, obj, matched_on), q),
            matched_on=matched_on,
        )

    @classmethod
    def _matched_fields(cls, service: type[EntityService], obj: Any, q: str | None) -> list[str]:
        """列出实际命中关键词的字段，让人能判断这条结果为什么被召回。"""
        if not q:
            return []
        needle = q.strip().lower()
        if not needle:
            return []
        return [
            field
            for field in service.search_fields
            if needle in str(getattr(obj, field, None) or "").lower()
        ]

    @classmethod
    def _snippet_text(
        cls, service: type[EntityService], obj: Any, matched_on: list[str]
    ) -> str | None:
        """优先取第一个命中字段的正文；没有关键词时退回标题。"""
        for field in matched_on:
            value = getattr(obj, field, None)
            if value:
                return str(value)
        title = getattr(obj, "title", None)
        return str(title) if title else None


def make_snippet(text: str | None, q: str | None, *, width: int = SNIPPET_WIDTH) -> str | None:
    """围绕关键词截取片段；关键词不在正文中时截取开头。"""
    if not text:
        return None
    if len(text) <= width:
        return text

    if q:
        index = text.lower().find(q.strip().lower())
        if index >= 0:
            start = max(0, index - width // 3)
            end = min(len(text), start + width)
            prefix = "…" if start > 0 else ""
            suffix = "…" if end < len(text) else ""
            return f"{prefix}{text[start:end]}{suffix}"

    return f"{text[:width]}…"


__all__ = ["ENTITY_SERVICES", "UnifiedSearchService", "make_snippet"]
