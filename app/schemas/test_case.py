"""测试用例 API Schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from app.models.enums import CaseType, Priority
from app.schemas.common import (
    BUSINESS_ID_PATTERN,
    BaseSchema,
    CaseTypeField,
    ORMInput,
    ORMUpdate,
    PriorityField,
    not_null_fields,
)


def _clean_tags(value: list[str]) -> list[str]:
    """去空白、去空串、保序去重。"""
    cleaned: list[str] = []
    for raw in value:
        tag = raw.strip()
        if tag and tag not in cleaned:
            cleaned.append(tag)
    return cleaned


class TestCaseCreate(ORMInput):
    """新增用例。"""

    __test__ = False  # 类名以 Test 开头，pytest 会误当作测试类收集，显式排除

    case_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=BUSINESS_ID_PATTERN,
        description="业务编号，如 LOGIN-001",
        examples=["LOGIN-001"],
    )
    title: str = Field(min_length=1, description="用例标题")
    module: str | None = Field(default=None, max_length=64, description="模块，如 login / payment")
    sub_module: str | None = Field(default=None, max_length=64)
    version: str | None = Field(default=None, max_length=32, description="适用版本，如 v1.3")
    priority: PriorityField = Field(default=None, description="优先级 P0-P3")
    case_type: CaseTypeField = Field(default=None, description="用例类型")
    precondition: str | None = Field(default=None, description="前置条件")
    steps: str | None = Field(default=None, description="测试步骤")
    expected_result: str | None = Field(default=None, description="预期结果")
    tags: list[str] = Field(default_factory=list, description="标签，自动去重去空白")
    source: str | None = Field(default=None, max_length=256, description="来源文件 / 系统")

    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, value: list[str]) -> list[str]:
        return _clean_tags(value)


class TestCaseUpdate(ORMUpdate):
    """局部更新用例：只更新显式传入的字段。"""

    __test__ = False  # 同上：避免被 pytest 误收集

    title: str | None = Field(default=None, min_length=1)
    module: str | None = Field(default=None, max_length=64)
    sub_module: str | None = Field(default=None, max_length=64)
    version: str | None = Field(default=None, max_length=32)
    priority: PriorityField = Field(default=None)
    case_type: CaseTypeField = Field(default=None)
    precondition: str | None = None
    steps: str | None = None
    expected_result: str | None = None
    tags: list[str] | None = None
    source: str | None = Field(default=None, max_length=256)

    _validate_not_null = not_null_fields("title", "tags")

    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _clean_tags(value)


class TestCaseRead(BaseSchema):
    """用例出参。"""

    __test__ = False  # 同上：避免被 pytest 误收集

    id: int
    case_id: str
    title: str
    module: str | None = None
    sub_module: str | None = None
    version: str | None = None
    priority: Priority | None = None
    case_type: CaseType | None = None
    precondition: str | None = None
    steps: str | None = None
    expected_result: str | None = None
    tags: list[str] = Field(default_factory=list)
    source: str | None = None
    created_at: datetime
    updated_at: datetime


class TestCaseListResponse(BaseSchema):
    """用例列表出参（保留显式模型，便于 OpenAPI 阅读）。"""

    __test__ = False  # 同上：避免被 pytest 误收集

    items: list[TestCaseRead]
    total: int
    limit: int
    offset: int


__all__ = ["TestCaseCreate", "TestCaseListResponse", "TestCaseRead", "TestCaseUpdate"]
