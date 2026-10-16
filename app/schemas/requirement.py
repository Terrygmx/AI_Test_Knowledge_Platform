"""需求 API Schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.models.enums import Priority, RequirementStatus
from app.schemas.common import (
    BUSINESS_ID_PATTERN,
    BaseSchema,
    ORMInput,
    ORMUpdate,
    PriorityField,
    RequirementStatusField,
    not_null_fields,
)


class RequirementCreate(ORMInput):
    """新增需求。"""

    req_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=BUSINESS_ID_PATTERN,
        description="业务编号，如 REQ-001",
        examples=["REQ-001"],
    )
    title: str = Field(min_length=1)
    module: str | None = Field(default=None, max_length=64)
    version: str | None = Field(default=None, max_length=32)
    priority: PriorityField = Field(default=None)
    status: RequirementStatusField = Field(default=None)
    content: str | None = Field(default=None, description="需求正文")
    source: str | None = Field(default=None, max_length=256)


class RequirementUpdate(ORMUpdate):
    """局部更新需求。"""

    title: str | None = Field(default=None, min_length=1)
    module: str | None = Field(default=None, max_length=64)
    version: str | None = Field(default=None, max_length=32)
    priority: PriorityField = Field(default=None)
    status: RequirementStatusField = Field(default=None)
    content: str | None = None
    source: str | None = Field(default=None, max_length=256)

    _validate_not_null = not_null_fields("title")


class RequirementRead(BaseSchema):
    """需求出参。"""

    id: int
    req_id: str
    title: str
    module: str | None = None
    version: str | None = None
    priority: Priority | None = None
    status: RequirementStatus | None = None
    content: str | None = None
    source: str | None = None
    created_at: datetime
    updated_at: datetime


__all__ = ["RequirementCreate", "RequirementRead", "RequirementUpdate"]
