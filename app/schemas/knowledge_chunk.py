"""KnowledgeChunk 请求 / 响应 Schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator

from app.schemas.common import BaseSchema, ORMInput

SourceType = Literal["testcase", "defect", "requirement", "document"]


class KnowledgeChunkCreate(ORMInput):
    """创建检索单元。Embedding 由服务层生成，API 入参不接收向量。"""

    source_type: SourceType
    source_id: str = Field(min_length=1, max_length=64)
    document_id: str | None = Field(default=None, max_length=64)
    chunk_index: int = Field(default=0, ge=0)
    content: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_hash: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        description="内容 SHA-256；缺省时由服务层计算",
    )
    version: str | None = Field(default=None, max_length=32)

    @field_validator("content")
    @classmethod
    def _content_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content 不能为空白")
        return value

    @field_validator("source_id")
    @classmethod
    def _source_id_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source_id 不能为空白")
        return value


class KnowledgeChunkRead(BaseSchema):
    """检索单元响应。默认不返回完整向量，避免 API 响应过大。"""

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )

    id: int
    source_type: SourceType
    source_id: str
    document_id: str | None
    chunk_index: int
    content: str
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias="meta",
        serialization_alias="metadata",
    )
    content_hash: str
    version: str | None
    created_at: datetime
    updated_at: datetime


__all__ = ["KnowledgeChunkCreate", "KnowledgeChunkRead", "SourceType"]
