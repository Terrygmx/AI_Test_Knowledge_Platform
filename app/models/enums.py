"""业务受控词表（Canonical Vocabulary）。

设计取舍：
1. 数据库列一律用 ``TEXT`` 保存，而**不用 PostgreSQL 原生 enum**——
   原生 enum 增删值要 ``ALTER TYPE``，迁移成本高；词表放代码里更稳。
2. 用 ``StrEnum`` 而不是 ``(str, Enum)``：前者的 ``str(member)`` 就是规范值
   （``"P0"``），后者会得到 ``"Priority.P0"``，一不小心就写脏数据。
   ``to_orm_dict()`` 仍然显式转成裸值，双保险。

由 Pydantic 负责校验与规范化；Phase 5 的 Normalizer 负责把外部系统
（Excel / 禅道 / Jira）里的各种写法映射到这里的规范值。

新增词表时同步更新 ``docs/`` 中数据模型章节。
"""

from __future__ import annotations

from enum import StrEnum


class Priority(StrEnum):
    """用例 / 缺陷 / 需求通用优先级。"""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class CaseType(StrEnum):
    """用例类型。"""

    SMOKE = "smoke"
    FUNCTIONAL = "functional"
    REGRESSION = "regression"
    PERFORMANCE = "performance"
    SECURITY = "security"
    COMPATIBILITY = "compatibility"


class Severity(StrEnum):
    """缺陷严重程度。"""

    BLOCKER = "blocker"
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    TRIVIAL = "trivial"


class DefectStatus(StrEnum):
    """缺陷状态。"""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    FIXED = "fixed"
    VERIFIED = "verified"
    CLOSED = "closed"
    REOPENED = "reopened"


class RequirementStatus(StrEnum):
    """需求状态。"""

    DRAFT = "draft"
    REVIEWING = "reviewing"
    APPROVED = "approved"
    DEPRECATED = "deprecated"
