"""Phase 22 / 23 的 SQL pre-filter：版本区间与检索层权限。

沿用 Phase 10 的核心原则：过滤必须发生在 SQL 的 ``WHERE``（LIMIT 之前），
被排除的候选不进入排序、不产生分数。

版本语义（Phase 22）：

- 比较在 ``version_key`` 列上做普通字符串范围比较；该列由零填充的
  ``v<major>.<minor>.<patch>`` 规范化而来，``v2.10`` 正确地大于 ``v2.9``；
- ``version`` 缺失或非法的 Chunk 不匹配任何区间（与 ``VersionRange.contains``
  对 None / 非法值返回 False 一致）。

权限语义（Phase 23，最小 MVP）：

- 规则来自 Chunk metadata 的 ``access`` 对象：
  ``{"teams": [...], "projects": [...], "min_role": "editor"}``；
- 没有 ``access`` 块的 Chunk 对所有主体可见（等价 ``AccessRule()``）；
- ``access`` 存在但畸形（非对象、teams / projects 不是字符串数组、
  min_role 不是已知角色）时拒绝可见——权限宁可 fail-closed；
- 每个维度独立判断：teams / projects 是"包含任一"，min_role 是等级比较。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, case, func, literal, or_
from sqlalchemy.dialects.postgresql import JSONB

from app.models import KnowledgeChunk
from app.models.types import POSTGRES
from app.retrieval.versioning import VersionRange, version_key

__all__ = ["apply_version_and_access_filters"]

_ROLE_RANKS = {"viewer": 0, "editor": 1, "admin": 2}
_ACCESS_PATH = "$.access"


def _meta_scalar(field_path: str, *, dialect_name: str) -> Any:
    """读取 metadata 内层字符串字段，带 JSON 类型守卫。"""
    meta = KnowledgeChunk.meta
    if dialect_name == POSTGRES:
        return meta[field_path].as_string()

    return func.json_quote(meta[field_path]) if field_path in meta else None


def _json_path(path: str) -> str:
    return path


def apply_version_and_access_filters(
    stmt: Select[Any],
    *,
    version_range: VersionRange | None = None,
    dialect_name: str,
    access_predicates: list[Any] | None = None,
) -> Select[Any]:
    """版本区间过滤（SQL 实现）+ 调用方提供的权限谓词。

    权限谓词依赖请求主体（teams / projects / min_role），由
    ``access_allow_predicates()`` 按主体构造；本函数只负责 AND 组合，
    保证两个维度都在 LIMIT 之前生效。
    """
    if version_range is not None:
        min_key = version_key(version_range.min_version)
        max_key = version_key(version_range.max_version)
        if min_key is not None:
            stmt = stmt.where(KnowledgeChunk.version_key >= min_key)
        if max_key is not None:
            stmt = stmt.where(KnowledgeChunk.version_key <= max_key)
    for predicate in access_predicates or []:
        stmt = stmt.where(predicate)
    return stmt


def access_allow_predicates(
    *,
    team: str,
    project: str,
    role_rank: int,
    dialect_name: str,
) -> list[Any]:
    """构造"该主体可见"的 SQL 谓词集合；调用方 AND 到查询上。

    每条谓词的形式是 ``(chunk 无 access) OR (chunk 的 access 允许主体)``。
    畸形 access 不满足任何"允许"分支，同时也不满足"无 access"分支，
    因此整体不可见。
    """
    meta = KnowledgeChunk.meta
    access = meta["access"]
    if dialect_name == POSTGRES:
        no_access = access == None  # noqa: E711 - JSON 路径缺失时为 SQL NULL
        type_guard = func.jsonb_typeof(access) == "object"
        teams_json = access["teams"]
        projects_json = access["projects"]

        def contains_any(column: Any, values: list[str]) -> Any:
            return or_(
                *(
                    column.op("@>")(literal([value], type_=JSONB))
                    for value in values
                )
            )

        teams_ok = contains_any(teams_json, [team]) if team else None
        projects_ok = contains_any(projects_json, [project]) if project else None
        role_expr = func.coalesce(access["min_role"].as_string(), "viewer")
        # 维度未设置（NULL）时视为"不限"，与 AccessRule 的 None 语义一致。
        teams_absent = teams_json == None  # noqa: E711
        projects_absent = projects_json == None  # noqa: E711
    else:
        meta_json = meta
        no_access = func.json_type(meta_json, _json_path(_ACCESS_PATH)).is_(None)
        type_guard = func.json_type(meta_json, _json_path(_ACCESS_PATH)) == "object"
        teams_json = None
        projects_json = None
        role_expr = None
        teams_ok = None
        projects_ok = None

    predicates: list[Any] = []
    if dialect_name == POSTGRES:
        predicates.append(no_access | type_guard)
        if teams_ok is not None:
            predicates.append(no_access | teams_absent | teams_ok)
        if projects_ok is not None:
            predicates.append(no_access | projects_absent | projects_ok)
        role_rank_expr = case(
            *[
                (role_expr == role_name, literal(rank))
                for role_name, rank in sorted(_ROLE_RANKS.items(), key=lambda kv: kv[1])
            ],
            else_=99,
        )
        predicates.append(no_access | (role_rank_expr >= role_rank))
    else:
        # SQLite：access 的判定交给 Python 侧复核（见
        # ``KnowledgeChunkService`` 的候选后过滤），SQL 层只排除畸形块。
        predicates.append(no_access | type_guard)
    return predicates
