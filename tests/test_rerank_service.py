"""Phase 11 Reranker 的输入 / 输出契约与重排边界。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.rerank.hybrid import rerank_fused_hits
from app.rerank.service import (
    OpenAICompatibleRerankClient,
    RerankCandidate,
    RerankedCandidate,
    RerankError,
)
from app.retrieval.hybrid import FusedHit


class FakeReranker:
    """确定性的降序重排器；用于测试身份还原，不模拟真实相关性。"""

    model = "fake-reranker"

    def rerank(self, query, candidates, *, top_n):  # noqa: ANN001, ANN202
        scored = sorted(
            ((-len(candidate.content), candidate.key) for candidate in candidates),
        )
        return [
            __import__("app.rerank.service", fromlist=["RerankedCandidate"]).RerankedCandidate(
                key=key,
                score=float(score),
            )
            for score, key in scored[:top_n]
        ]


def _hit(key: tuple[str, str, int], content: str) -> FusedHit:
    return FusedHit(
        source_type=key[0],
        source_id=key[1],
        chunk_index=key[2],
        score=0.0,
        content=content,
        vector_rank=None,
        keyword_rank=None,
        vector_score=None,
        keyword_score=None,
        matched_terms=(),
    )


def test_rerank_preserves_hybrid_candidates_and_orders_hits() -> None:
    hits = [
        _hit(("testcase", "SHORT", 0), "short"),
        _hit(("testcase", "LONG", 1), "much longer content"),
    ]
    reranked = rerank_fused_hits(hits, "query", FakeReranker(), top_n=2)

    assert [hit.source_id for hit in reranked] == ["LONG", "SHORT"]


def test_rerank_allows_valid_short_success_response() -> None:
    class ShortReranker:
        model = "fake-reranker"

        def rerank(self, query, candidates, *, top_n):  # noqa: ANN001, ANN202
            return [RerankedCandidate(key=candidates[-1].key, score=1.0)]

    hits = [_hit(("testcase", "A", 0), "a"), _hit(("testcase", "B", 1), "b")]
    reranked = rerank_fused_hits(hits, "query", ShortReranker(), top_n=2)

    assert [hit.source_id for hit in reranked] == ["B"]


def test_rerank_rejects_unknown_top_n_and_duplicate_identity() -> None:
    with pytest.raises(ValueError, match="top_n"):
        rerank_fused_hits([], "query", FakeReranker(), top_n=0)

    duplicate = [
        _hit(("testcase", "A", 0), "x"),
        _hit(("testcase", "A", 0), "y"),
    ]
    with pytest.raises(ValueError, match="身份重复"):
        rerank_fused_hits(duplicate, "query", FakeReranker(), top_n=1)


def test_client_rejects_empty_query_and_blank_documents() -> None:
    settings = Settings(
        rerank_base_url="http://127.0.0.1:28080/v1",
        rerank_api_key="omlx",
        rerank_model="fake",
    )
    client = OpenAICompatibleRerankClient(settings)
    candidates = [RerankCandidate(key="a", content="content")]

    with pytest.raises(RerankError, match="query 不能为空白"):
        client.rerank(" ", candidates, top_n=1)
    with pytest.raises(RerankError, match="内容不能为空白"):
        client.rerank("query", [RerankCandidate(key="a", content=" ")], top_n=1)


def test_client_allows_success_with_fewer_than_top_n_results() -> None:
    settings = Settings(
        rerank_base_url="http://127.0.0.1:28080/v1",
        rerank_api_key="omlx",
        rerank_model="fake",
    )
    client = OpenAICompatibleRerankClient(settings)

    class FakeResponse:
        results = [type("Result", (), {"index": 1, "relevance_score": 0.7})()]

    original_post = client._client.post

    def post(*args, **kwargs):
        return FakeResponse()

    client._client.post = post
    try:
        ranked = client.rerank(
            "query",
            [RerankCandidate(key="a", content="a"), RerankCandidate(key="b", content="b")],
            top_n=2,
        )
    finally:
        client._client.post = original_post

    assert [(item.key, item.score) for item in ranked] == [("b", pytest.approx(0.7))]


def test_client_rejects_empty_results_for_non_empty_candidates() -> None:
    settings = Settings(
        rerank_base_url="http://127.0.0.1:28080/v1",
        rerank_api_key="omlx",
        rerank_model="fake",
    )
    client = OpenAICompatibleRerankClient(settings)

    class FakeResponse:
        results = []

    client._client.post = lambda *args, **kwargs: FakeResponse()

    with pytest.raises(RerankError, match="返回空结果"):
        client.rerank("query", [RerankCandidate(key="a", content="a")], top_n=1)


def test_upstream_error_exposes_safe_error_details() -> None:
    settings = Settings(
        rerank_base_url="http://127.0.0.1:28080/v1",
        rerank_api_key="omlx",
        rerank_model="fake",
    )
    client = OpenAICompatibleRerankClient(settings)

    class ProbeError(Exception):
        status_code = 409

    def fail_post(*args, **kwargs):
        raise ProbeError("model unavailable")

    client._client.post = fail_post

    with pytest.raises(RerankError) as exc_info:
        client.rerank("query", [RerankCandidate(key="probe", content="probe")], top_n=1)

    assert exc_info.value.detail["error_type"] == "ProbeError"
    assert exc_info.value.detail["status_code"] == 409


def test_availability_probe_uses_single_candidate_rerank() -> None:
    settings = Settings(
        rerank_base_url="http://127.0.0.1:28080/v1",
        rerank_api_key="omlx",
        rerank_model="fake",
    )
    client = OpenAICompatibleRerankClient(settings)
    calls = []

    def fake_rerank(query, candidates, *, top_n):
        calls.append((query, len(candidates), top_n))
        return [RerankedCandidate(key="probe", score=1.0)]

    client.rerank = fake_rerank  # type: ignore[method-assign]
    client.verify_available()

    assert calls == [("availability probe", 1, 1)]


def test_rerank_response_contract_requires_index_and_score() -> None:
    from app.rerank.service import _RerankResponse

    payload = {"results": [{"index": 0, "relevance_score": 0.8}]}
    parsed = _RerankResponse.model_validate(payload)
    assert parsed.results[0].relevance_score == pytest.approx(0.8)

    with pytest.raises(ValidationError):
        _RerankResponse.model_validate({"results": [{"id": 0}]})
