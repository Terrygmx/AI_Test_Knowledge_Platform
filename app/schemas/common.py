"""Schema 公共基类：入参清洗、ORM 输出、分页、错误响应。

两个关键约定：
1. ``to_orm_dict()`` 用 ``model_dump(mode="json")`` —— 枚举必须转成裸值再进数据库，
   因为 ``str(Priority.P0)`` 得到的是 ``"Priority.P0"`` 而不是 ``"P0"``；
2. 所有入参的字符串字段自动去首尾空白，避免 ``" LOGIN-001 "`` 这种脏数据入库。
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.models.enums import (
    CaseType,
    DefectStatus,
    Priority,
    RequirementStatus,
    Severity,
)

T = TypeVar("T")

SourceType = Literal["testcase", "defect", "requirement", "document"]

#: 业务编号（Case ID / Bug ID / Req ID / Doc ID）的合法字符集
BUSINESS_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]*$"


class BaseSchema(BaseModel):
    """所有 Schema 的基类：支持从 ORM 对象读取，字段名与别名互通。"""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class ORMInput(BaseSchema):
    """入参基类。"""

    @model_validator(mode="before")
    @classmethod
    def _strip_text_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        return {
            key: value.strip() if isinstance(value, str) else value for key, value in data.items()
        }

    def to_orm_dict(self, *, exclude_unset: bool = False) -> dict[str, Any]:
        """转成 ORM 可用的 dict。

        ``mode="json"`` 让枚举序列化为裸值（``"P0"`` 而非 ``Priority.P0``）。
        ``exclude_unset=True`` 用于局部更新：只有调用方显式传了的字段才会出现。
        """
        return self.model_dump(mode="json", exclude_unset=exclude_unset)


class ORMUpdate(ORMInput):
    """更新入参基类。

    ``extra="forbid"``：拼错字段名 / 试图改不可变字段（如 Case ID）时直接 422，
    而不是被 silent ignore——后者会让调用方以为改成功了。

    注意：业务编号（Case ID / Bug ID / Req ID / Doc ID）**不可通过 API 修改**，
    它是引用出处（Citation）的锚点；服务层保留该能力仅供内部索引用。
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True, extra="forbid")

    def to_orm_dict(self, *, exclude_unset: bool = True) -> dict[str, Any]:
        """更新语义下默认 ``exclude_unset=True``。

        这是刻意的安全默认：``exclude_unset=False`` 会把没传的字段一并 dump 成
        ``None``，一次 PATCH 就能把整行清空（而且 NOT NULL 列会直接炸）。
        """
        return super().to_orm_dict(exclude_unset=exclude_unset)


def not_null_fields(*names: str):
    """生成字段校验器：这些字段不允许被显式置空。

    背景：NOT NULL 列如果被置成 ``None``，要等到 ``flush`` 才炸 ``IntegrityError``，
    调用方收到的是 500；在 Schema 层拦下来，拿到的是可读的 422。
    局部更新语义不受影响——不传该字段时压根不会走这个校验器。
    """

    @field_validator(*names)
    @classmethod
    def _validate(cls, value: Any) -> Any:  # noqa: ANN401
        if value is None:
            raise ValueError("该字段不允许置空（对应数据库列 NOT NULL）")
        return value

    return _validate


class Pagination(BaseModel):
    """分页参数（``?limit=&offset=``）。"""

    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class Page(BaseModel, Generic[T]):
    """统一分页响应。"""

    items: list[T]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)

    @classmethod
    def build(cls, *, items: Iterable[T], total: int, limit: int, offset: int) -> Page[T]:
        return cls(items=list(items), total=total, limit=limit, offset=offset)


class ErrorBody(BaseModel):
    """错误响应体，统一信封 ``{"error": {...}}``。"""

    code: str = Field(description="机器可读错误码，如 not_found / duplicate")
    message: str = Field(description="人类可读错误信息")
    request_id: str | None = Field(default=None, description="请求追踪 ID")
    detail: Any | None = Field(default=None, description="附加细节，如字段级校验错误")


class ErrorResponse(BaseModel):
    """错误信封，用于 OpenAPI 文档。"""

    error: ErrorBody


def normalize_enum(value: Any, enum_cls: type[Enum]) -> Any:
    """把 ``"p0"`` / ``"Smoke"`` / ``" IN_PROGRESS "`` 规范化为枚举成员。

    无法识别时原样返回，由 Pydantic 产出标准校验错误（HTTP 422）。
    """
    if value is None or isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        text = value.strip()
        for member in enum_cls:
            if member.value.lower() == text.lower():
                return member
    return value


def _enum_field(enum_cls: type[Enum]) -> Any:
    return Annotated[enum_cls | None, BeforeValidator(lambda v: normalize_enum(v, enum_cls))]


# 各受控词表的规范化字段类型：入参写 "p0" / "Smoke" 都能被归一成规范枚举值
PriorityField = _enum_field(Priority)
CaseTypeField = _enum_field(CaseType)
SeverityField = _enum_field(Severity)
DefectStatusField = _enum_field(DefectStatus)
RequirementStatusField = _enum_field(RequirementStatus)


def enum_query(enum_cls: type[Enum]) -> Any:
    """查询参数枚举：复用入参归一化，保持 OpenAPI 枚举文档。"""
    return Annotated[enum_cls | None, BeforeValidator(lambda v: normalize_enum(v, enum_cls))]

__all__ = [
    "BUSINESS_ID_PATTERN",
    "BaseSchema",
    "CaseTypeField",
    "DefectStatusField",
    "ErrorBody",
    "ErrorResponse",
    "ORMInput",
    "Page",
    "Pagination",
    "PriorityField",
    "RequirementStatusField",
    "SeverityField",
    "SourceType",
    "enum_query",
    "normalize_enum",
]
