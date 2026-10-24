"""Phase 15 Query Understanding Schema、确定性解析与 LLM 重试测试。"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.query.understanding import (
    DeterministicQueryUnderstanding,
    LLMQueryUnderstanding,
    QueryUnderstanding,
    QueryUnderstandingError,
)


class FakeLLM:
    def __init__(self, answers: list[str]) -> None:
        self.answers = answers
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0)


def test_deterministic_understanding_extracts_filters_and_intent() -> None:
    result = DeterministicQueryUnderstanding().understand(
        "登录模块 P0 v1.3 的短信登录冒烟用例有哪些？"
    )

    assert result.intent == "testcase_search"
    assert result.module == "login"
    assert result.priority.value == "P0"
    assert result.version == "v1.3"
    assert "SHOP" not in result.semantic_query
    assert result.semantic_query == "登录模块 P0 v1.3 的短信登录冒烟用例有哪些？"
    assert result.accepted_by == "deterministic"
    assert result.to_metadata_filters() == {
        "module": "login",
        "priority": "P0",
        "version": "v1.3",
    }


def test_identifier_is_preserved_and_intent_is_detected() -> None:
    result = DeterministicQueryUnderstanding().understand("BUG-1234 的根因是什么")

    assert result.intent == "defect_search"
    assert "BUG-1234" in result.keywords
    assert "BUG-1234" in result.semantic_query


def test_blank_query_is_rejected() -> None:
    with pytest.raises(QueryUnderstandingError, match="空白"):
        DeterministicQueryUnderstanding().understand("   ")


def test_schema_rejects_unknown_filter_fields() -> None:
    with pytest.raises(ValidationError, match="未知 Metadata"):
        QueryUnderstanding(semantic_query="登录", filters={"unknown": "x"})


def test_schema_deduplicates_keywords_and_normalizes_blank_fields() -> None:
    result = QueryUnderstanding(
        semantic_query="登录",
        module=" login ",
        version=" v1.3 ",
        keywords=["登录", "登录", " P0 "],
    )

    assert result.module == "login"
    assert result.version == "v1.3"
    assert result.keywords == ["登录", "P0"]


def test_llm_understanding_accepts_fenced_json() -> None:
    llm = FakeLLM(
        [
            """```json
{"intent":"testcase_search","module":"login","priority":"P0",
"version":null,"keywords":["登录"],"semantic_query":"登录 P0 用例"}
```"""
        ]
    )

    result = LLMQueryUnderstanding(llm).understand("登录 P0 用例")

    assert result.intent == "testcase_search"
    assert result.priority.value == "P0"
    assert result.accepted_by == "llm"
    assert len(llm.prompts) == 1


def test_llm_understanding_retries_invalid_json_then_succeeds() -> None:
    llm = FakeLLM(
        [
            "not json",
            json.dumps(
                {
                    "intent": "document_qa",
                    "module": None,
                    "priority": None,
                    "version": None,
                    "keywords": ["支付"],
                    "semantic_query": "支付失败排查",
                }
            ),
        ]
    )

    result = LLMQueryUnderstanding(llm, max_retries=1).understand("支付失败怎么排查")

    assert result.intent == "document_qa"
    assert len(llm.prompts) == 2
    assert result.accepted_by == "llm-retry-1"


def test_llm_understanding_falls_back_after_invalid_output() -> None:
    fallback = DeterministicQueryUnderstanding()
    llm = FakeLLM(["invalid", "still invalid"])

    result = LLMQueryUnderstanding(llm, max_retries=1, fallback=fallback).understand(
        "登录 P0 用例"
    )

    assert result.intent == "testcase_search"
    assert result.module == "login"
    assert result.accepted_by == "deterministic"


def test_llm_understanding_rejects_non_object_json() -> None:
    llm = FakeLLM(["[]", "[]"])

    with pytest.raises(QueryUnderstandingError, match="JSON 对象"):
        LLMQueryUnderstanding(llm, max_retries=1).understand("登录")


def test_llm_understanding_rejects_multiple_json_blocks() -> None:
    llm = FakeLLM(
        [
            '```json\n{"semantic_query":"登录"}\n```\n```json\n{"semantic_query":"支付"}\n```',
            '{"semantic_query":"登录","keywords":[]}',
        ]
    )

    result = LLMQueryUnderstanding(llm, max_retries=1).understand("登录")

    assert result.semantic_query == "登录"
    assert result.accepted_by == "llm-retry-1"


def test_llm_understanding_rejects_unknown_fields() -> None:
    llm = FakeLLM(
        [
            json.dumps(
                {
                    "intent": "testcase_search",
                    "semantic_query": "登录",
                    "made_up_filter": "payment",
                }
            ),
            json.dumps(
                {
                    "intent": "testcase_search",
                    "semantic_query": "登录",
                    "keywords": [],
                }
            ),
        ]
    )

    result = LLMQueryUnderstanding(llm, max_retries=1).understand("登录")

    assert result.semantic_query == "登录"
    assert result.accepted_by == "llm-retry-1"
