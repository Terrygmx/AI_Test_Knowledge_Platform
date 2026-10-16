"""文档 API Schema。

ORM 侧属性名是 ``meta``（列名 ``metadata``），API 契约仍叫 ``metadata``，
通过 ``validation_alias`` 对齐；``to_orm_dict()`` 负责把字段名换回 ORM 属性名。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import AliasChoices, Field

from app.schemas.common import (
    BUSINESS_ID_PATTERN,
    BaseSchema,
    ORMInput,
    ORMUpdate,
    not_null_fields,
)


class DocumentCreate(ORMInput):
    """新增文档元数据（正文由 Phase 2 的 Chunker 处理）。"""

    doc_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=BUSINESS_ID_PATTERN,
        description="业务编号，如 DOC-001",
        examples=["DOC-001"],
    )
    title: str | None = Field(default=None, description="文档标题")
    doc_type: str | None = Field(
        default=None, max_length=32, description="markdown / pdf / spec ..."
    )
    path: str | None = Field(default=None, max_length=512, description="原始文件路径")
    content_hash: str | None = Field(
        default=None, max_length=64, description="内容指纹（增量索引用）"
    )
    version: str | None = Field(default=None, max_length=32)
    metadata: dict[str, Any] = Field(default_factory=dict, description="附加元数据")

    def to_orm_dict(self, *, exclude_unset: bool = False) -> dict[str, Any]:
        data = super().to_orm_dict(exclude_unset=exclude_unset)
        if "metadata" in data:
            data["meta"] = data.pop("metadata")
        return data


class DocumentUpdate(ORMUpdate):
    """局部更新文档元数据。"""

    title: str | None = None
    doc_type: str | None = Field(default=None, max_length=32)
    path: str | None = Field(default=None, max_length=512)
    content_hash: str | None = Field(default=None, max_length=64)
    version: str | None = Field(default=None, max_length=32)
    metadata: dict[str, Any] | None = None

    _validate_not_null = not_null_fields("metadata")

    def to_orm_dict(self, *, exclude_unset: bool = False) -> dict[str, Any]:
        data = super().to_orm_dict(exclude_unset=exclude_unset)
        if "metadata" in data:
            data["meta"] = data.pop("metadata")
        return data


class DocumentRead(BaseSchema):
    """文档出参。"""

    id: int
    doc_id: str
    title: str | None = None
    doc_type: str | None = None
    path: str | None = None
    content_hash: str | None = None
    version: str | None = None
    metadata: dict[str, Any] = Field(
        default_factory=dict, validation_alias=AliasChoices("meta", "metadata")
    )
    created_at: datetime
    updated_at: datetime


__all__ = ["DocumentCreate", "DocumentRead", "DocumentUpdate"]
