"""Phase 15 Query Understanding。

核心边界：

- 结构化输出必须先通过 Pydantic 校验，不能让 LLM 的自由文本直接进入检索；
- 确定性规则是默认基线，LLM 只作为可替换增强；
- 解析出的 filter 只能使用现有 Metadata Filter 白名单；
- ``semantic_query`` 是检索用改写文本，不能伪造业务编号或 Gold 证据。
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any, Literal, Protocol

from pydantic import Field, ValidationError, field_validator

from app.generation.llm import LLMClient, LLMError
from app.models.enums import Priority
from app.retrieval.filters import normalize_metadata_filters
from app.retrieval.lexical import tokenize
from app.schemas.common import BaseSchema

QueryIntent = Literal[
    "testcase_search",
    "defect_search",
    "document_qa",
    "requirement_search",
    "general_search",
]

_INTENT_TERMS: tuple[tuple[QueryIntent, tuple[str, ...]], ...] = (
    ("testcase_search", ("用例", "测试用例", "case", "testcase", "步骤", "预期结果")),
    ("defect_search", ("缺陷", "bug", "defect", "复现", "根因", "修复")),
    ("requirement_search", ("需求", "requirement", "规格", "验收标准")),
    ("document_qa", ("文档", "规范", "手册", "说明", "doc", "document")),
)
_PRIORITY_PATTERN = re.compile(r"(?<![A-Za-z0-9])P([0-3])(?![A-Za-z0-9])", re.IGNORECASE)
_VERSION_PATTERN = re.compile(r"(?<![A-Za-z0-9])v?(\d+\.\d+(?:\.\d+)?)(?![A-Za-z0-9])", re.IGNORECASE)
_IDENTIFIER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:SHOP-)?(?:TC|BUG|REQ|DOC)-[A-Za-z0-9_-]+",
    re.IGNORECASE,
)
_MODULE_ALIASES: dict[str, str] = {
    "登录": "login",
    "login": "login",
    "购物车": "cart",
    "cart": "cart",
    "支付": "payment",
    "payment": "payment",
    "结算": "checkout",
    "checkout": "checkout",
    "退款": "refund",
    "refund": "refund",
    "商品": "catalog",
    "catalog": "catalog",
}


class QueryUnderstandingError(ValueError):
    """Query Understanding 输出不合法或无法解析。"""


class QueryUnderstanding(BaseSchema):
    """Phase 15 的结构化查询契约。"""

    intent: QueryIntent = "general_search"
    module: str | None = None
    priority: Priority | None = None
    version: str | None = None
    keywords: list[str] = Field(default_factory=list)
    semantic_query: str = Field(min_length=1)
    filters: dict[str, str | list[str]] = Field(default_factory=dict)
    accepted_by: str = Field(default="deterministic", min_length=1)

    @field_validator("module", "version")
    @classmethod
    def _not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("字段不能为空白")
        return stripped

    @field_validator("keywords")
    @classmethod
    def _normalize_keywords(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            keyword = value.strip()
            if not keyword:
                raise ValueError("keywords 不能包含空白项")
            key = keyword.casefold()
            if key not in seen:
                seen.add(key)
                normalized.append(keyword)
        return normalized

    @field_validator("filters")
    @classmethod
    def _validate_filters(cls, values: dict[str, str | list[str]]) -> dict[str, str | list[str]]:
        return normalize_metadata_filters(values)

    def to_metadata_filters(self) -> dict[str, str | list[str]]:
        """把结构化字段投影为 Metadata Filter；显式 filters 优先。"""
        projected = dict(self.filters)
        if self.module is not None:
            projected.setdefault("module", self.module)
        if self.priority is not None:
            projected.setdefault("priority", self.priority.value)
        if self.version is not None:
            projected.setdefault("version", self.version)
        return normalize_metadata_filters(projected)


class QueryUnderstandingService(Protocol):
    """可替换的 Query Understanding 实现。"""

    name: str

    def understand(self, query: str) -> QueryUnderstanding: ...


def _dedupe(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        key = normalized.casefold()
        if normalized and key not in seen:
            seen.add(key)
            result.append(normalized)
    return result


class DeterministicQueryUnderstanding:
    """不调用 LLM 的规则基线，适合离线回归与失败降级。"""

    name = "deterministic"

    def understand(self, query: str) -> QueryUnderstanding:
        text = query.strip()
        if not text:
            raise QueryUnderstandingError("query 不能为空白")

        lowered = text.casefold()
        intent: QueryIntent = "general_search"
        for candidate, terms in _INTENT_TERMS:
            if any(term in lowered for term in terms):
                intent = candidate
                break

        module = next(
            (canonical for alias, canonical in _MODULE_ALIASES.items() if alias in lowered),
            None,
        )
        priority_match = _PRIORITY_PATTERN.search(text)
        priority = Priority(f"P{priority_match.group(1)}") if priority_match else None
        version_match = _VERSION_PATTERN.search(text)
        version = f"v{version_match.group(1)}" if version_match else None

        identifiers = _dedupe(
            match.group(0).upper() for match in _IDENTIFIER_PATTERN.finditer(text)
        )
        lexical_tokens = [
            token for token in tokenize(text) if len(token) >= 2 and not token.isdigit()
        ]
        keyword_tokens = [
            token
            for token in lexical_tokens
            if not (
                len(token) == 2
                and all("\u4e00" <= char <= "\u9fff" for char in token)
                and token
                not in {"登录", "支付", "退款", "购物车", "结算", "用例", "缺陷", "需求"}
            )
        ]
        keywords = _dedupe([*identifiers, *keyword_tokens])[:20]
        # 关键词用于解释和后续 BM25，但 semantic_query 保留原句，避免把
        # 中文 bigram 碎片当成“语义改写”后反而降低向量召回。
        semantic_query = text
        return QueryUnderstanding(
            intent=intent,
            module=module,
            priority=priority,
            version=version,
            keywords=keywords,
            semantic_query=semantic_query,
        )


def _extract_json_object(text: str) -> dict[str, Any]:
    """从结构化输出中提取唯一 JSON 对象；不做猜测性字段修正。"""
    stripped = text.strip()
    fenced_blocks = re.findall(
        r"```(?:json)?\s*(.*?)```",
        stripped,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if len(fenced_blocks) > 1:
        raise QueryUnderstandingError("LLM 返回了多个 JSON 代码块")
    if stripped.startswith("```"):
        stripped = fenced_blocks[0].strip() if fenced_blocks else stripped
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError as exc:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise QueryUnderstandingError("LLM 未返回 JSON 对象") from exc
        try:
            value = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as nested_exc:
            raise QueryUnderstandingError("LLM 返回的 JSON 无法解析") from nested_exc
    if not isinstance(value, dict):
        raise QueryUnderstandingError("LLM 输出必须是 JSON 对象")
    return value


class LLMQueryUnderstanding:
    """通过 LLM 生成结构化 Query Understanding。

    JSON 解析和 Schema 校验失败时会重试；重试耗尽后抛错，由调用方决定回退
    到确定性解析。不能把非法字段静默丢弃后继续检索。
    """

    name = "llm"

    def __init__(
        self,
        llm: LLMClient,
        *,
        max_retries: int = 2,
        fallback: QueryUnderstandingService | None = None,
        accepted_by: str = "llm",
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries 必须 >= 0")
        self._llm = llm
        self._max_retries = max_retries
        self._fallback = fallback
        if not accepted_by.strip():
            raise ValueError("accepted_by 不能为空白")
        self._accepted_by = accepted_by.strip()

    def understand(self, query: str) -> QueryUnderstanding:
        text = query.strip()
        if not text:
            raise QueryUnderstandingError("query 不能为空白")
        prompt = (
            "把测试知识检索问题解析为 JSON。只输出 JSON，不要 Markdown。\n"
            "字段：intent(testcase_search|defect_search|document_qa|requirement_search|"
            "general_search), module(string|null), priority(P0|P1|P2|P3|null), "
            "version(string|null), keywords(string[]), semantic_query(string)。\n"
            "不要编造业务编号；不确定的字段填 null 或空数组。\n"
            f"<query>{text}</query>"
        )
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                payload = _extract_json_object(self._llm.generate(prompt))
                # LLM 输出字段名必须严格来自 Schema；额外字段不能静默混入。
                unknown = set(payload) - set(QueryUnderstanding.model_fields)
                if unknown:
                    raise QueryUnderstandingError(
                        f"LLM 返回了未知字段：{', '.join(sorted(unknown))}"
                    )
                return QueryUnderstanding.model_validate(
                    {
                        **payload,
                        "accepted_by": (
                            self._accepted_by
                            if attempt == 0
                            else f"{self._accepted_by}-retry-{attempt}"
                        ),
                    }
                )
            except (LLMError, ValidationError, QueryUnderstandingError) as exc:
                last_error = exc
        if self._fallback is not None:
            return self._fallback.understand(text)
        raise QueryUnderstandingError(
            f"LLM Query Understanding 在重试后仍不合法：{last_error}"
        ) from last_error


__all__ = [
    "DeterministicQueryUnderstanding",
    "LLMQueryUnderstanding",
    "QueryIntent",
    "QueryUnderstanding",
    "QueryUnderstandingError",
    "QueryUnderstandingService",
]
