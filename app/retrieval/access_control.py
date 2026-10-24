"""Phase 23 权限：检索层访问控制契约。

MVP 只有三个维度：``team`` / ``project`` / ``role``。核心原则是
**Retrieval 层过滤**：不允许召回后再隐藏，因为分数分布和 Top-K 截断
会泄露被过滤内容的存在。

当前只有纯逻辑实现；数据库行级过滤需要等权限字段进入数据模型后，
把同样的语义翻译到 SQL（Phase 10 的 pre-filter 模式）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from enum import StrEnum
from typing import TypeVar

from pydantic import Field, model_validator

from app.schemas.common import BaseSchema


class Role(StrEnum):
    """最小角色集合；只做分层能力判断，不做细粒度 RBAC。"""

    VIEWER = "viewer"
    EDITOR = "editor"
    ADMIN = "admin"


_ROLE_RANK = {Role.VIEWER: 0, Role.EDITOR: 1, Role.ADMIN: 2}

_T = TypeVar("_T")


class AccessContext(BaseSchema):
    """一次检索请求的访问主体。"""

    team: str = Field(min_length=1)
    project: str = Field(min_length=1)
    role: Role


class AccessRule(BaseSchema):
    """一条资源可见性规则；None 表示该维度不限。"""

    teams: list[str] | None = None
    projects: list[str] | None = None
    min_role: Role = Role.VIEWER

    @model_validator(mode="after")
    def _normalize(self) -> AccessRule:
        for name in ("teams", "projects"):
            values = getattr(self, name)
            if values is not None:
                cleaned = [value.strip() for value in values if value.strip()]
                if not cleaned:
                    raise ValueError(f"{name} 不能是空列表；不限请传 None")
                setattr(self, name, cleaned)
        return self

    def allows(self, context: AccessContext) -> bool:
        """判断访问主体是否满足规则；所有已设置维度都必须通过。"""
        if self.teams is not None and context.team not in self.teams:
            return False
        if self.projects is not None and context.project not in self.projects:
            return False
        return _ROLE_RANK[context.role] >= _ROLE_RANK[self.min_role]


def filter_accessible(
    items: Sequence[_T],
    *,
    context: AccessContext,
    rule_of: Callable[[_T], AccessRule | None],
) -> list[_T]:
    """在返回候选前过滤；被排除项不进入排序，也不产生分数。"""
    kept: list[_T] = []
    for item in items:
        rule = rule_of(item)
        if rule is None or rule.allows(context):
            kept.append(item)
    return kept


__all__ = ["AccessContext", "AccessRule", "Role", "filter_accessible"]
