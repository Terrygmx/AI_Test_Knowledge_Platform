"""跨方言可移植的列类型。

目标数据库是 PostgreSQL + pgvector，但测试套件必须能在没有 Docker / PostgreSQL 的
环境下完整运行（SQLite 内存库）。因此这里用 ``TypeDecorator`` 做方言分流：

- PostgreSQL：使用原生 ``JSONB`` / ``TEXT[]``；
- 其他方言（SQLite）：退化为 JSON 文本，读写语义保持一致。

约定：新增列类型统一在这里声明，业务代码里不写 ``if dialect == "postgresql"``。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import JSON, BigInteger, Integer, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.types import TypeDecorator, TypeEngine

POSTGRES = "postgresql"


def pk_type() -> TypeEngine:
    """自增主键列类型。

    PG -> ``BIGSERIAL``；SQLite -> ``INTEGER``。
    直接写 ``BigInteger`` 时 SQLite 会渲染成 ``BIGINT``，它不是 rowid 别名，
    导致 ``NOT NULL constraint failed: <table>.id``。这是 SQLite 兼容性的经典坑。
    """
    return BigInteger().with_variant(Integer, "sqlite")


class JSONMap(TypeDecorator):
    """JSON 对象列：PG -> ``JSONB``，SQLite -> ``JSON``。"""

    impl = JSONB
    cache_ok = True

    def load_dialect_impl(self, dialect):  # noqa: ANN001, ANN201
        if dialect.name == POSTGRES:
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value: dict[str, Any] | None, dialect):  # noqa: ANN001
        if value is None:
            return None
        return dict(value)

    def process_result_value(self, value: dict[str, Any] | None, dialect):  # noqa: ANN001
        if value is None:
            return None
        return dict(value)


class TextArray(TypeDecorator):
    """字符串数组列：PG -> ``TEXT[]``，SQLite -> JSON 文本。

    PG 侧保留原生数组，以便建 GIN 索引；SQLite 侧序列化为 JSON 文本，
    ``process_result_value`` 保证读出来仍是 ``list[str]``。
    """

    impl = ARRAY(Text)
    cache_ok = True

    def load_dialect_impl(self, dialect):  # noqa: ANN001, ANN201
        if dialect.name == POSTGRES:
            return dialect.type_descriptor(ARRAY(Text))
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value: list[str] | None, dialect):  # noqa: ANN001
        if value is None:
            return None
        if dialect.name == POSTGRES:
            return list(value)
        return json.dumps(list(value), ensure_ascii=False)

    def process_result_value(self, value: list[str] | str | None, dialect):  # noqa: ANN001
        if value is None:
            return None
        if isinstance(value, str):
            return list(json.loads(value))
        return list(value)
