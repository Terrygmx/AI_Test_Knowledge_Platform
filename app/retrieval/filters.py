"""Phase 10 Metadata Filter：受控字段、输入校验与跨方言 SQL 构造。

语义约定：

- 所有过滤字段之间是 AND；
- ``module`` / ``sub_module`` / ``priority`` / ``version`` / ``status`` /
  ``severity`` / ``case_type`` / ``doc_type`` 为标量精确匹配；
- ``tags`` / ``related_cases`` 为数组“包含任一值”；
- 缺失字段、``null``、错误类型都不匹配，不能把“没有元数据”当成满足条件；
- 未知字段、空数组和空白值直接报错，避免调用方以为过滤生效。

SQLite 使用 ``json_each`` 展开数组，PostgreSQL 使用 JSONB ``@>``。
两种实现都在 SQL 的 ``WHERE`` 中执行，调用方必须在 ``LIMIT`` 之前应用。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import Select, cast, exists, func, literal, or_, select
from sqlalchemy.dialects.postgresql import JSONB

from app.models import KnowledgeChunk
from app.models.types import POSTGRES

SCALAR_FILTER_FIELDS = frozenset(
    {
        "module",
        "sub_module",
        "priority",
        "version",
        "status",
        "severity",
        "case_type",
        "doc_type",
    }
)
ARRAY_FILTER_FIELDS = frozenset({"tags", "related_cases"})
SUPPORTED_FILTER_FIELDS = SCALAR_FILTER_FIELDS | ARRAY_FILTER_FIELDS


class MetadataFilterError(ValueError):
    """Metadata Filter 输入不合法；调用方不应静默忽略。"""


def normalize_metadata_filters(
    filters: Mapping[str, object] | None,
) -> dict[str, str | list[str]]:
    """校验并规范化过滤条件。

    返回新的 dict / list，避免调用方修改输入后影响已构造的查询。
    标量保留大小写，因为 ``priority`` 等值由 Schema 层负责归一化；
    ``tags`` 本身是用户自定义词，不应强制小写。
    """
    if filters is None:
        return {}
    if not isinstance(filters, Mapping):
        raise MetadataFilterError("metadata_filters 必须是对象")
    if not filters:
        return {}

    normalized: dict[str, str | list[str]] = {}
    for raw_field, raw_value in filters.items():
        field = str(raw_field).strip()
        if not field:
            raise MetadataFilterError("过滤字段名不能为空白")
        if field not in SUPPORTED_FILTER_FIELDS:
            raise MetadataFilterError(
                f"未知 Metadata 过滤字段：{field}",
                # 注意：不把原始值写进错误，避免把用户数据回显到日志 / 响应。
            )

        if field in SCALAR_FILTER_FIELDS:
            if not isinstance(raw_value, str):
                raise MetadataFilterError(f"{field} 是标量字段，值必须是字符串")
            value = raw_value.strip()
            if not value:
                raise MetadataFilterError(f"{field} 不能为空白")
            normalized[field] = value
            continue

        if not isinstance(raw_value, (list, tuple)):
            raise MetadataFilterError(f"{field} 是数组字段，值必须是字符串数组")
        if not raw_value:
            raise MetadataFilterError(f"{field} 不能为空数组")
        values: list[str] = []
        seen: set[str] = set()
        for item in raw_value:
            if not isinstance(item, str):
                raise MetadataFilterError(f"{field} 的值必须是字符串")
            value = item.strip()
            if not value:
                raise MetadataFilterError(f"{field} 不能包含空白值")
            if value not in seen:
                seen.add(value)
                values.append(value)
        normalized[field] = values
    return normalized


def _json_path(field: str) -> str:
    """JSON path 只由受控字段名构造，不能拼接用户输入。"""
    return f"$.{field}"


def _scalar_predicate(field: str, value: str):
    # ``as_string`` 在 PG 编译为 ->>，在 SQLite 编译为 JSON_EXTRACT + CAST。
    return KnowledgeChunk.meta[field].as_string() == value


def _array_predicate(field: str, values: list[str], dialect_name: str):
    if dialect_name == POSTGRES:
        # JSONB 数组的 ``@>`` 是“包含全部”，而本接口约定是“包含任一值”。
        # 不能把多个值塞进一个数组后直接 @>；否则 tags=[smoke,security]
        # 会要求两个标签同时存在。这里对每个值构造单元素数组包含，再用 OR。
        return or_(
            *(
                KnowledgeChunk.meta[field].op("@>")(cast(literal(_json_array([value])), JSONB))
                for value in values
            )
        )

    # SQLite：json_each 在 SQL 层展开数组。缺失 / 标量 / null 都不会产生
    # 匹配的 value；空数组已在 normalize 阶段拒绝。
    elements = func.json_each(KnowledgeChunk.meta, _json_path(field)).table_valued(
        "key",
        "value",
    )
    return exists(
        select(literal(1))
        .select_from(elements)
        .where(
            func.json_type(KnowledgeChunk.meta, _json_path(field)) == "array",
            elements.c.value.in_(values),
        )
    )


def _json_array(values: list[str]) -> str:
    import json

    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def metadata_filter_predicates(
    filters: Mapping[str, object] | None,
    *,
    dialect_name: str,
) -> list[Any]:
    """返回一组 SQLAlchemy 谓词；调用方负责 AND 到查询上。"""
    normalized = normalize_metadata_filters(filters)
    predicates: list[Any] = []
    for field, value in normalized.items():
        if field in SCALAR_FILTER_FIELDS:
            assert isinstance(value, str)
            predicates.append(_scalar_predicate(field, value))
        else:
            assert isinstance(value, list)
            predicates.append(_array_predicate(field, value, dialect_name))
    return predicates


def apply_metadata_filters_to_table(
    stmt: Select[Any],
    metadata_column: Any,
    filters: Mapping[str, object] | None,
    *,
    dialect_name: str,
) -> Select[Any]:
    """把同一套过滤语义应用到任意 JSON 列（实验表 / 兼容表）。"""
    if not filters:
        return stmt
    normalized = normalize_metadata_filters(filters)
    for field_name, expected in normalized.items():
        if field_name in SCALAR_FILTER_FIELDS:
            assert isinstance(expected, str)
            predicate = metadata_column[field_name].as_string() == expected
        elif dialect_name == POSTGRES:
            assert isinstance(expected, list)
            predicate = or_(
                *(
                    metadata_column[field_name].op("@>")(
                        cast(literal(_json_array([value])), JSONB)
                    )
                    for value in expected
                )
            )
        else:
            assert isinstance(expected, list)
            elements = func.json_each(
                metadata_column,
                _json_path(field_name),
            ).table_valued("key", "value")
            predicate = exists(
                select(literal(1))
                .select_from(elements)
                .where(
                    func.json_type(metadata_column, _json_path(field_name)) == "array",
                    elements.c.value.in_(expected),
                )
            )
        stmt = stmt.where(predicate)
    return stmt


def apply_metadata_filters(
    stmt: Select[Any],
    filters: Mapping[str, object] | None,
    *,
    dialect_name: str,
) -> Select[Any]:
    """把 Metadata Filter 加到查询的 WHERE，且不改变已有排序 / LIMIT。"""
    if not filters:
        return stmt
    for predicate in metadata_filter_predicates(filters, dialect_name=dialect_name):
        stmt = stmt.where(predicate)
    return stmt


__all__ = [
    "ARRAY_FILTER_FIELDS",
    "MetadataFilterError",
    "SCALAR_FILTER_FIELDS",
    "SUPPORTED_FILTER_FIELDS",
    "apply_metadata_filters",
    "apply_metadata_filters_to_table",
    "metadata_filter_predicates",
    "normalize_metadata_filters",
]
