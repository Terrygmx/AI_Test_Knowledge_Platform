"""Phase 16 Query Rewrite。

重写只改变检索文本，不改变 Gold、过滤条件或引用来源。默认提供 no-op 基线，
LLM 改写与确定性改写都必须可单独评测；没有可测收益时不得设为默认。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.generation.llm import LLMClient, LLMError


@dataclass(frozen=True)
class RewriteResult:
    """一次 Query Rewrite 的结果与可追溯信息。"""

    original_query: str
    search_query: str
    strategy: str
    changed: bool
    fallback_reason: str | None = None


class QueryRewriter(Protocol):
    """可替换的 Query Rewrite 实现。"""

    name: str

    def rewrite(self, query: str) -> RewriteResult: ...


class NoOpQueryRewriter:
    """基线：原样返回 Query。"""

    name = "noop"

    def rewrite(self, query: str) -> RewriteResult:
        text = query.strip()
        if not text:
            raise ValueError("query 不能为空白")
        return RewriteResult(
            original_query=text,
            search_query=text,
            strategy=self.name,
            changed=False,
        )


class DeterministicQueryRewriter:
    """轻量规则改写：清理重复空白、统一全角空格，不做语义扩写。"""

    name = "deterministic"

    def __init__(self, aliases: dict[str, str] | None = None) -> None:
        self._aliases = {
            key.strip().casefold(): value.strip()
            for key, value in (aliases or {}).items()
            if key.strip() and value.strip()
        }

    def rewrite(self, query: str) -> RewriteResult:
        text = " ".join(query.replace("\u3000", " ").split())
        if not text:
            raise ValueError("query 不能为空白")
        rewritten = text
        for source, target in self._aliases.items():
            rewritten = re.sub(
                re.escape(source),
                lambda _match, replacement=target: replacement,
                rewritten,
                flags=re.IGNORECASE,
            )
        rewritten = " ".join(rewritten.split())
        return RewriteResult(
            original_query=text,
            search_query=rewritten,
            strategy=self.name,
            changed=rewritten != text,
        )


class LLMQueryRewriter:
    """LLM 改写器；输出必须是非空单行检索文本。"""

    name = "llm"

    def __init__(
        self,
        llm: LLMClient,
        *,
        fallback: QueryRewriter | None = None,
        max_retries: int = 1,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries 必须 >= 0")
        self._llm = llm
        self._fallback = fallback or NoOpQueryRewriter()
        self._max_retries = max_retries
        self._fallback_reason: str | None = None

    @property
    def fallback_reason(self) -> str | None:
        """最近一次 rewrite 是否发生降级；只用于可观测性。"""
        return self._fallback_reason

    def rewrite(self, query: str) -> RewriteResult:
        text = query.strip()
        if not text:
            raise ValueError("query 不能为空白")
        self._fallback_reason = None
        prompt = (
            "把下面的测试知识检索问题改写成更适合关键词与向量检索的查询。\n"
            "只输出一行查询文本，不要解释，不要编造编号、版本或结论。\n"
            f"<query>{text}</query>"
        )
        for _ in range(self._max_retries + 1):
            try:
                raw_output = self._llm.generate(prompt).strip()
                if raw_output and "\n" not in raw_output:
                    output = " ".join(raw_output.split())
                    return RewriteResult(
                        original_query=text,
                        search_query=output,
                        strategy=self.name,
                        changed=output != text,
                    )
            except LLMError:
                pass
        fallback_result = self._fallback.rewrite(text)
        self._fallback_reason = "llm_rewrite_failed"
        return RewriteResult(
            original_query=fallback_result.original_query,
            search_query=fallback_result.search_query,
            strategy=fallback_result.strategy,
            changed=fallback_result.changed,
            fallback_reason="llm_rewrite_failed",
        )


class CompositeQueryRewriter:
    """按顺序尝试多个改写器，便于 A/B 或降级组合。"""

    name = "composite"

    def __init__(self, rewriters: Sequence[QueryRewriter]) -> None:
        if not rewriters:
            raise ValueError("至少需要一个 QueryRewriter")
        self._rewriters = list(rewriters)

    def rewrite(self, query: str) -> RewriteResult:
        result: RewriteResult | None = None
        for rewriter in self._rewriters:
            result = rewriter.rewrite(query)
            if result.changed:
                return result
        assert result is not None
        return result


__all__ = [
    "CompositeQueryRewriter",
    "DeterministicQueryRewriter",
    "LLMQueryRewriter",
    "NoOpQueryRewriter",
    "QueryRewriter",
    "RewriteResult",
]
