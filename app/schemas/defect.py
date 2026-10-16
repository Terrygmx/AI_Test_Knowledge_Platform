"""缺陷 API Schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from app.models.enums import DefectStatus, Priority, Severity
from app.schemas.common import (
    BUSINESS_ID_PATTERN,
    BaseSchema,
    DefectStatusField,
    ORMInput,
    ORMUpdate,
    PriorityField,
    SeverityField,
    not_null_fields,
)


class DefectCreate(ORMInput):
    """新增缺陷。"""

    bug_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=BUSINESS_ID_PATTERN,
        description="业务编号，如 BUG-1021",
        examples=["BUG-1021"],
    )
    title: str = Field(min_length=1, description="缺陷标题")
    module: str | None = Field(default=None, max_length=64)
    version: str | None = Field(default=None, max_length=32, description="发现版本")
    severity: SeverityField = Field(default=None, description="严重程度")
    priority: PriorityField = Field(default=None, description="优先级 P0-P3")
    status: DefectStatusField = Field(default=None, description="缺陷状态")
    reproduce_steps: str | None = Field(default=None, description="复现步骤")
    root_cause: str | None = Field(default=None, description="根因分析")
    resolution: str | None = Field(default=None, description="解决方案")
    related_cases: list[str] = Field(default_factory=list, description="关联用例编号")
    source: str | None = Field(default=None, max_length=256)

    @field_validator("related_cases")
    @classmethod
    def _clean_related_cases(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in value:
            case_id = raw.strip()
            if case_id and case_id not in cleaned:
                cleaned.append(case_id)
        return cleaned


class DefectUpdate(ORMUpdate):
    """局部更新缺陷。"""

    title: str | None = Field(default=None, min_length=1)
    module: str | None = Field(default=None, max_length=64)
    version: str | None = Field(default=None, max_length=32)
    severity: SeverityField = Field(default=None)
    priority: PriorityField = Field(default=None)
    status: DefectStatusField = Field(default=None)
    reproduce_steps: str | None = None
    root_cause: str | None = None
    resolution: str | None = None
    related_cases: list[str] | None = None
    source: str | None = Field(default=None, max_length=256)

    _validate_not_null = not_null_fields("title", "related_cases")

    @field_validator("related_cases")
    @classmethod
    def _clean_related_cases(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned: list[str] = []
        for raw in value:
            case_id = raw.strip()
            if case_id and case_id not in cleaned:
                cleaned.append(case_id)
        return cleaned


class DefectRead(BaseSchema):
    """缺陷出参。"""

    id: int
    bug_id: str
    title: str
    module: str | None = None
    version: str | None = None
    severity: Severity | None = None
    priority: Priority | None = None
    status: DefectStatus | None = None
    reproduce_steps: str | None = None
    root_cause: str | None = None
    resolution: str | None = None
    related_cases: list[str] = Field(default_factory=list)
    source: str | None = None
    created_at: datetime
    updated_at: datetime


__all__ = ["DefectCreate", "DefectRead", "DefectUpdate"]
