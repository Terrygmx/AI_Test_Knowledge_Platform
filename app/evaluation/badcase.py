"""Phase 19 Badcase Store：统一分类、根因假设与回归闭环。

本模块只做进程内记录与分类，不接数据库表。每个 Badcase 都必须带：

- ``stage``：失败发生的 Pipeline 阶段；
- ``root_cause``：人工或规则判定的根因；
- ``hypothesis``：修复假设；
- ``regression_query_ids``：修复后必须复跑的评测样本。

闭环状态是显式的：``open`` → ``fixing`` → ``fixed`` → ``verified``。
跳过 Evaluation 直接标记 ``verified`` 会破坏回归语义。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from app.schemas.common import BaseSchema


class BadcaseStage(StrEnum):
    """Badcase 归因的 Pipeline 阶段。"""

    PARSER = "parser"
    CHUNK = "chunk"
    EMBEDDING = "embedding"
    QUERY = "query"
    RETRIEVAL = "retrieval"
    RERANK = "rerank"
    CONTEXT = "context"
    GENERATION = "generation"
    CITATION = "citation"


class BadcaseStatus(StrEnum):
    """闭环状态；只能按 open → fixing → fixed → verified 顺序推进。"""

    OPEN = "open"
    FIXING = "fixing"
    FIXED = "fixed"
    VERIFIED = "verified"


_STATUS_ORDER = {
    BadcaseStatus.OPEN: 0,
    BadcaseStatus.FIXING: 1,
    BadcaseStatus.FIXED: 2,
    BadcaseStatus.VERIFIED: 3,
}


class Badcase(BaseSchema):
    """一条可闭环跟踪的 Badcase。"""

    model_config = BaseSchema.model_config | {"validate_assignment": True}

    badcase_id: str = Field(min_length=1)
    query_id: str = Field(min_length=1)
    stage: BadcaseStage
    symptom: str = Field(min_length=1)
    root_cause: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    regression_query_ids: list[str] = Field(default_factory=list)
    status: BadcaseStatus = BadcaseStatus.OPEN
    evaluation_result: dict[str, Any] | None = None
    notes: str | None = None

    @field_validator("regression_query_ids")
    @classmethod
    def _require_regression_ids(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("badcase 必须绑定至少一条回归 Query")
        return values

    @model_validator(mode="after")
    def _validate_verified_has_evaluation(self) -> Badcase:
        if self.status is BadcaseStatus.VERIFIED and self.evaluation_result is None:
            raise ValueError("verified 状态必须附带 evaluation_result")
        return self

    def advance(self, *, evaluation_result: dict[str, Any] | None = None) -> None:
        """按顺序推进闭环状态；verified 必须有修复后的评测结果。"""
        order = _STATUS_ORDER[self.status]
        if order >= _STATUS_ORDER[BadcaseStatus.VERIFIED]:
            raise ValueError("badcase 已经验证完成，不能继续推进")
        next_status = list(BadcaseStatus)[order + 1]
        if next_status is BadcaseStatus.VERIFIED:
            if evaluation_result is None:
                raise ValueError("推进到 verified 必须提供 evaluation_result")
            self.evaluation_result = evaluation_result
        self.status = next_status


class BadcaseStore:
    """进程内 Badcase 记录与闭环管理。"""

    def __init__(self) -> None:
        self._items: dict[str, Badcase] = {}

    def add(self, badcase: Badcase) -> Badcase:
        if badcase.badcase_id in self._items:
            raise ValueError(f"badcase_id 重复：{badcase.badcase_id}")
        self._items[badcase.badcase_id] = badcase
        return badcase

    def get(self, badcase_id: str) -> Badcase:
        item = self._items.get(badcase_id)
        if item is None:
            raise KeyError(f"badcase 不存在：{badcase_id}")
        return item

    def list(self, *, stage: BadcaseStage | None = None, status: BadcaseStatus | None = None) -> list[Badcase]:
        return [
            item
            for item in self._items.values()
            if (stage is None or item.stage is stage)
            and (status is None or item.status is status)
        ]

    def open_count(self) -> int:
        return sum(item.status is not BadcaseStatus.VERIFIED for item in self._items.values())

