"""Phase 25 的真实检索工具：证据来自 Hybrid Search 与实体查询。

设计约束：

- 证据字符串必须带来源标识（``source_type:source_id``），Planner 与上层
  LLM 不能只拿到裸文本——没有来源的文本不算证据（Evidence First）；
- 工具只读不写；检索参数经白名单校验（source_type 枚举、limit 上限）；
- 查询失败返回 ``ToolResult(errors=[...])`` 而不是抛异常，让 Agent 循环
  能记录失败并继续。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agent.tools import ToolResult
from app.core.exceptions import DomainError
from app.retrieval.embedding import get_embedding_client
from app.retrieval.versioning import VersionRange, parse_version
from app.services.defect_service import DefectService
from app.services.document_service import DocumentService
from app.services.knowledge_chunk_service import KnowledgeChunkService
from app.services.requirement_service import RequirementService
from app.services.search_service import make_snippet
from app.services.testcase_service import TestCaseService

_SOURCE_TYPES = {"testcase", "defect", "requirement", "document"}
_MAX_LIMIT = 20


def _limit(arguments: dict[str, Any]) -> int:
    limit = int(arguments.get("limit", 5))
    if limit < 1 or limit > _MAX_LIMIT:
        raise ValueError(f"limit 必须在 1..{_MAX_LIMIT}")
    return limit


def _query_of(arguments: dict[str, Any]) -> str:
    query = str(arguments.get("query", "")).strip()
    if not query:
        raise ValueError("query 不能为空白")
    return query


class HybridSearchTool:
    """Chunk 级 Hybrid 检索（Vector + BM25 融合）。"""

    name = "hybrid_search"

    def __init__(self, session: Session) -> None:
        self._session = session

    def run(self, arguments: dict[str, Any]) -> ToolResult:
        try:
            query = _query_of(arguments)
            limit = _limit(arguments)
            source_types = arguments.get("source_types")
            if source_types is not None:
                unknown = set(source_types) - _SOURCE_TYPES
                if unknown:
                    raise ValueError(f"未知来源类型：{sorted(unknown)}")
            embedding = get_embedding_client()
            hits = KnowledgeChunkService.hybrid_search(
                self._session,
                query=query,
                query_embedding=embedding.embed_query(query),
                limit=limit,
                source_types=source_types,
            )
        except (DomainError, ValueError, KeyError, TypeError) as exc:
            return ToolResult(tool=self.name, errors=[str(exc)])
        evidence = [
            f"{hit.source_type}:{hit.source_id}#{hit.chunk_index} {make_snippet(hit.content, query)}"
            for hit in hits
        ]
        return ToolResult(tool=self.name, evidence=evidence)


class _EntitySearchTool:
    """实体查询基类：统一检索服务的工具化包装。"""

    entity_name = "entity"
    service: Any = None

    def __init__(self, session: Session) -> None:
        self._session = session

    def run(self, arguments: dict[str, Any]) -> ToolResult:
        try:
            query = _query_of(arguments)
            limit = _limit(arguments)
            rows, _total = self.service.list(self._session, q=query, limit=limit)
        except (DomainError, ValueError, KeyError, TypeError) as exc:
            return ToolResult(tool=self.name, errors=[str(exc)])
        unique_field = self.service.unique_field
        evidence = [
            f"{self.entity_name}:{getattr(row, unique_field)} {self._text(row, query)}"
            for row in rows
        ]
        return ToolResult(tool=self.name, evidence=evidence)

    def _text(self, row: Any, query: str) -> str:
        title = getattr(row, "title", None) or ""
        fields = " ".join(
            str(getattr(row, field) or "")
            for field in self.service.search_fields
        )
        return make_snippet(f"{title} {fields}".strip(), query) or title


class SearchTestCasesTool(_EntitySearchTool):
    name = "search_testcases"
    entity_name = "testcase"
    service = TestCaseService


class SearchDefectsTool(_EntitySearchTool):
    name = "search_defects"
    entity_name = "defect"
    service = DefectService


class SearchRequirementsTool(_EntitySearchTool):
    name = "search_requirements"
    entity_name = "requirement"
    service = RequirementService


class SearchDocumentsTool(_EntitySearchTool):
    name = "search_documents"
    entity_name = "document"
    service = DocumentService


class CompareVersionsTool:
    """按版本区间检索 Chunk，支撑跨版本对比类问题。"""

    name = "compare_versions"

    def __init__(self, session: Session) -> None:
        self._session = session

    def run(self, arguments: dict[str, Any]) -> ToolResult:
        try:
            base = str(arguments.get("base_version", "")).strip()
            target = str(arguments.get("target_version", "")).strip()
            if not base or not target:
                raise ValueError("必须提供 base_version 与 target_version")
            parse_version(base)
            parse_version(target)
            low, high = sorted((base, target), key=parse_version)
            query = str(arguments.get("query", "")).strip() or f"{low}..{high} 变化"
            limit = _limit(arguments)
            embedding = get_embedding_client()
            hits = KnowledgeChunkService.hybrid_search(
                self._session,
                query=query,
                query_embedding=embedding.embed_query(query),
                limit=limit,
                version_range=VersionRange(min_version=low, max_version=high),
            )
        except (DomainError, ValueError, KeyError, TypeError) as exc:
            return ToolResult(tool=self.name, errors=[str(exc)])
        evidence = [
            f"{hit.source_type}:{hit.source_id}#{hit.chunk_index} v-range=[{low},{high}] "
            f"{make_snippet(hit.content, query)}"
            for hit in hits
        ]
        return ToolResult(tool=self.name, evidence=evidence)
