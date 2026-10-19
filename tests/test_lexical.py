"""Phase 8 词法检索：分词、倒排索引与 BM25。"""

from __future__ import annotations

import math

import pytest

from app.retrieval.lexical import (
    BM25Index,
    InvertedIndex,
    LexicalDocument,
    tokenize,
)


class TestTokenizer:
    def test_keeps_ascii_identifiers_versions_and_priorities_whole(self) -> None:
        assert tokenize("P0 BUG-1234 login-api v1.3") == [
            "p0",
            "bug-1234",
            "login-api",
            "v1.3",
        ]

    def test_chinese_uses_unigrams_and_bigrams(self) -> None:
        tokens = tokenize("支付超时")

        assert tokens == ["支", "付", "超", "时", "支付", "付超", "超时"]

    def test_mixed_text_keeps_identifier_and_chinese_ngrams(self) -> None:
        tokens = tokenize("P0 BUG-1234 login-api v1.3 支付超时")

        assert tokens[:4] == ["p0", "bug-1234", "login-api", "v1.3"]
        assert "支付" in tokens
        assert "超时" in tokens

    def test_normalizes_width_and_case(self) -> None:
        assert tokenize("ＢＵＧ－１２３４ Login-API") == ["bug-1234", "login-api"]

    def test_keeps_unicode_latin_words_and_identifiers_whole(self) -> None:
        assert tokenize("Café café-déjà") == ["café", "café-déjà"]

    def test_unicode_word_next_to_chinese_still_splits_at_script_boundary(self) -> None:
        assert tokenize("café中文") == ["café", "中", "文", "中文"]

    def test_empty_and_punctuation_only_text(self) -> None:
        assert tokenize("") == []
        assert tokenize("，。！？ \n") == []


def _documents() -> list[LexicalDocument]:
    return [
        LexicalDocument(
            source_type="defect",
            source_id="BUG-1234",
            content="P0 BUG-1234 login-api v1.3 登录接口偶发超时",
        ),
        LexicalDocument(
            source_type="testcase",
            source_id="TC-2001",
            content="P1 登录接口验证码校验",
        ),
        LexicalDocument(
            source_type="document",
            source_id="DOC-1",
            content="支付超时后使用原幂等键查询交易状态",
        ),
    ]


class TestInvertedIndex:
    def test_posting_lists_map_terms_to_documents_and_frequencies(self) -> None:
        index = InvertedIndex(_documents())

        assert index.document_count == 3
        assert index.postings["bug-1234"] == {0: 1}
        assert index.postings["登录"] == {0: 1, 1: 1}
        assert index.document_frequencies["登录"] == 2

    def test_term_lookup_rejects_unknown_term(self) -> None:
        index = InvertedIndex(_documents())

        assert index.documents_for("不存在") == {}

    def test_term_lookup_tokenizes_mixed_query_text(self) -> None:
        index = InvertedIndex(_documents())

        # 四个 unigram + 三个 bigram；返回的是命中 term 的频次总和，
        # 不是“文档长度”或去重后的词项数。
        assert index.documents_for("登录接口") == {0: 7, 1: 7}


class TestBM25:
    def test_exact_identifier_outranks_partial_chinese_match(self) -> None:
        index = BM25Index(_documents())

        hits = index.search("BUG-1234", limit=3)

        assert [hit.document.source_id for hit in hits] == ["BUG-1234"]
        assert hits[0].score > 0
        assert hits[0].matched_terms == ["bug-1234"]

    def test_combined_query_uses_all_terms_and_is_explainable(self) -> None:
        index = BM25Index(_documents())

        hits = index.search("P0 BUG-1234 login-api v1.3", limit=3)

        assert hits[0].document.source_id == "BUG-1234"
        assert hits[0].matched_terms == ["p0", "bug-1234", "login-api", "v1.3"]
        assert set(hits[0].term_scores) == set(hits[0].matched_terms)

    def test_chinese_matched_terms_are_tokenized_terms_not_business_concepts(self) -> None:
        index = BM25Index(_documents())

        hits = index.search("支付超时", limit=3)

        assert hits[0].document.source_id == "DOC-1"
        assert hits[0].matched_terms == ["支", "付", "超", "时", "支付", "付超", "超时"]

    def test_rare_identifier_has_higher_idf_than_common_chinese_term(self) -> None:
        index = BM25Index(_documents())

        assert index.idf("bug-1234") > index.idf("登录")

    def test_empty_query_returns_no_results(self) -> None:
        index = BM25Index(_documents())

        assert index.search("   ") == []

    def test_limit_must_be_positive(self) -> None:
        index = BM25Index(_documents())

        with pytest.raises(ValueError, match="limit"):
            index.search("登录", limit=0)

    def test_score_formula_matches_bm25(self) -> None:
        documents = [
            LexicalDocument(source_type="testcase", source_id="A", content="登录 登录"),
            LexicalDocument(source_type="testcase", source_id="B", content="登录 支付"),
        ]
        index = BM25Index(documents, k1=1.2, b=0.75)

        hit = index.search("登录", limit=1)[0]

        # 分别核对两个命中 term；查询“登录”自身会产生 unigram + bigram。
        average_length = 6
        expected_terms = {
            "登": (2, math.log(1 + (2 - 2 + 0.5) / (2 + 0.5))),
            "录": (2, math.log(1 + (2 - 2 + 0.5) / (2 + 0.5))),
            "登录": (2, math.log(1 + (2 - 2 + 0.5) / (2 + 0.5))),
        }
        expected = 0.0
        for tf, idf in expected_terms.values():
            expected += idf * (
                tf * (1.2 + 1)
                / (tf + 1.2 * (1 - 0.75 + 0.75 * 6 / average_length))
            )
        assert hit.score == pytest.approx(expected)

    def test_ties_are_deterministic(self) -> None:
        documents = [
            LexicalDocument(source_type="testcase", source_id="B", content="登录"),
            LexicalDocument(source_type="testcase", source_id="A", content="登录"),
        ]
        index = BM25Index(documents)

        assert [hit.document.source_id for hit in index.search("登录")] == ["A", "B"]
