"""Phase 16 Query Rewrite 契约、降级与稳定性测试。"""

from __future__ import annotations

import pytest

from app.query.rewrite import (
    CompositeQueryRewriter,
    DeterministicQueryRewriter,
    LLMQueryRewriter,
    NoOpQueryRewriter,
)


class FakeLLM:
    def __init__(self, answers: list[str]) -> None:
        self.answers = answers
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0)


def test_noop_rewriter_preserves_query() -> None:
    result = NoOpQueryRewriter().rewrite(" 支付失败怎么排查？ ")

    assert result.search_query == "支付失败怎么排查？"
    assert result.changed is False
    assert result.strategy == "noop"


def test_deterministic_rewriter_normalizes_whitespace_and_aliases() -> None:
    rewriter = DeterministicQueryRewriter({"短信登录": "sms login"})
    result = rewriter.rewrite("  短信登录\u3000P0  用例 ")

    assert result.search_query == "sms login P0 用例"
    assert result.changed is True


def test_deterministic_rewriter_aliases_are_case_insensitive() -> None:
    rewriter = DeterministicQueryRewriter({"SMS": "short message"})

    result = rewriter.rewrite("sms login")

    assert result.search_query == "short message login"
    assert result.changed is True


def test_llm_rewriter_accepts_single_line_output() -> None:
    llm = FakeLLM(["支付超时 幂等键 查询交易状态"])
    result = LLMQueryRewriter(llm).rewrite("支付超时以后还能不能重新点支付？")

    assert result.search_query == "支付超时 幂等键 查询交易状态"
    assert result.strategy == "llm"
    assert result.changed is True


def test_llm_rewriter_falls_back_on_multiline_or_empty_output() -> None:
    llm = FakeLLM(["第一行\n第二行", ""])
    fallback = DeterministicQueryRewriter({"支付超时": "payment timeout"})
    result = LLMQueryRewriter(llm, fallback=fallback, max_retries=1).rewrite(
        "支付超时怎么处理"
    )

    assert result.search_query == "payment timeout怎么处理"
    assert result.fallback_reason == "llm_rewrite_failed"
    assert result.strategy == "deterministic"


def test_composite_rewriter_uses_first_changed_result() -> None:
    composite = CompositeQueryRewriter(
        [NoOpQueryRewriter(), DeterministicQueryRewriter({"登录": "login"})]
    )
    result = composite.rewrite("登录失败")

    assert result.search_query == "login失败"
    assert result.changed is True


def test_blank_query_is_rejected_by_all_rewriters() -> None:
    with pytest.raises(ValueError, match="空白"):
        NoOpQueryRewriter().rewrite(" ")
    with pytest.raises(ValueError, match="空白"):
        DeterministicQueryRewriter().rewrite("\n")
