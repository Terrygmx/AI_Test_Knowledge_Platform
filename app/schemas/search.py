"""统一检索 API Schema（Phase 1 最小版）。

注意：本阶段**不返回 score**。向量检索 / BM25 / RRF / Rerank 在 Phase 3/8/9/11 才引入，
在没有打分依据时给出相似度数字等于伪造证据，违反「Evidence First」原则。
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.models.enums import Priority
from app.schemas.common import BaseSchema, SourceType


class SearchHit(BaseSchema):
    """一条检索命中。

    ``matched_on`` 说明命中了哪些字段（title / steps / content ...），
    让人能判断这条结果为什么被召回，也是后续 Badcase 归因的输入。
    """

    source_type: SourceType
    source_id: str = Field(description="业务编号：Case ID / Bug ID / Req ID / Doc ID")
    title: str
    module: str | None = None
    version: str | None = None
    priority: Priority | None = None
    severity: str | None = None
    status: str | None = None
    snippet: str | None = Field(default=None, description="命中片段（围绕关键词截取）")
    matched_on: list[str] = Field(default_factory=list)


class SearchResponse(BaseSchema):
    """检索响应。"""

    query: str | None = None
    filters: dict[str, Any] = Field(default_factory=dict, description="实际生效的过滤条件")
    total: int
    limit: int
    offset: int
    items: list[SearchHit] = Field(default_factory=list)


__all__ = ["SearchHit", "SearchResponse", "SourceType"]
