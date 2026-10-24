"""Phase 11 Hybrid + Rerank 实验的排序与延迟边界。"""

from __future__ import annotations

import pytest

from app.evaluation.dataset import load_dataset
from app.evaluation.hybrid_experiment import HybridCandidate, evaluate_hybrid
from app.evaluation.lexical_experiment import retrieve_chunks
from app.rerank.service import RerankCandidate, RerankedCandidate, RerankError
from scripts import rerank_experiment

DATASET = "eval/fixtures/phase11_simulated.json"


class DeterministicReranker:
    """按内容长度重排，便于验证 Rerank 只影响顺序。"""

    model = "fake-reranker"

    def __init__(self, *, calls: list[int] | None = None) -> None:
        self.calls = calls if calls is not None else []

    def rerank(self, query, candidates, *, top_n):  # noqa: ANN001, ANN202
        self.calls.append(len(candidates))
        ordered = sorted(candidates, key=lambda item: (-len(item.content), item.key))
        return [
            RerankedCandidate(key=item.key, score=float(-rank))
            for rank, item in enumerate(ordered[:top_n])
        ]


def _candidates() -> list[HybridCandidate]:
    documents = retrieve_chunks(load_dataset(DATASET))
    return [
        HybridCandidate(
            source_type=item.source_type,
            source_id=item.source_id,
            chunk_index=item.chunk_index,
            content=item.content,
            embedding=(1.0, 0.0),
        )
        for item in documents
    ]


def test_hybrid_and_rerank_share_candidate_set() -> None:
    dataset = load_dataset(DATASET)
    candidates = _candidates()[:5]
    query_embeddings = [(1.0, 0.0)] * len(dataset.queries)
    reranker = DeterministicReranker()

    result = evaluate_hybrid(
        dataset,
        candidates,
        query_embeddings,
        top_k=2,
        candidate_k=5,
        fusion="rrf",
        reranker=reranker,
        rerank_candidates=5,
    )

    assert [channel.name for channel in result.channels] == ["rrf", "rrf_rerank"]
    assert all(call == 5 for call in reranker.calls)
    rerank_channel = result.channels[1]
    assert rerank_channel.reranked is True
    assert rerank_channel.rerank_input_candidates == len(dataset.queries) * 5
    assert rerank_channel.rerank_returned_candidates == len(dataset.queries) * 2
    assert rerank_channel.rerank_empty_responses == 0
    assert result.rerank_seconds == rerank_channel.rerank_seconds


def test_rerank_uses_full_candidate_depth_not_final_top_k() -> None:
    dataset = load_dataset(DATASET)
    candidates = _candidates()[:7]
    query_embeddings = [(1.0, 0.0)] * len(dataset.queries)
    reranker = DeterministicReranker()

    result = evaluate_hybrid(
        dataset,
        candidates,
        query_embeddings,
        top_k=2,
        candidate_k=7,
        fusion="rrf",
        reranker=reranker,
        rerank_candidates=5,
    )

    assert all(call == 5 for call in reranker.calls)
    baseline, rerank_channel = result.channels
    assert not baseline.reranked
    assert baseline.rerank_input_candidates == 0
    assert rerank_channel.rerank_input_candidates == len(dataset.queries) * 5


def test_rerank_depth_cannot_exceed_candidate_depth() -> None:
    dataset = load_dataset(DATASET)
    with pytest.raises(ValueError, match="rerank_candidates 不能大于 candidate_k"):
        evaluate_hybrid(
            dataset,
            _candidates()[:2],
            [(1.0, 0.0)] * len(dataset.queries),
            top_k=1,
            candidate_k=2,
            fusion="rrf",
            reranker=DeterministicReranker(),
            rerank_candidates=3,
        )


def test_rerank_requires_candidates_and_min_depth() -> None:
    dataset = load_dataset(DATASET)
    candidates = _candidates()[:2]
    query_embeddings = [(1.0, 0.0)] * len(dataset.queries)

    with pytest.raises(ValueError, match="同时提供"):
        evaluate_hybrid(
            dataset,
            candidates,
            query_embeddings,
            top_k=1,
            candidate_k=2,
            fusion="rrf",
            reranker=DeterministicReranker(),
        )
    with pytest.raises(ValueError, match="不能小于 top_k"):
        evaluate_hybrid(
            dataset,
            candidates,
            query_embeddings,
            top_k=2,
            candidate_k=2,
            fusion="rrf",
            reranker=DeterministicReranker(),
            rerank_candidates=1,
        )


def test_rerank_candidate_identity_must_match_fusion() -> None:
    class UnknownReranker(DeterministicReranker):
        def rerank(self, query, candidates, *, top_n):  # noqa: ANN001, ANN202
            return [RerankedCandidate(key="missing", score=1.0)]

    dataset = load_dataset(DATASET)
    candidates = _candidates()[:2]
    with pytest.raises(RerankError, match="未知候选"):
        evaluate_hybrid(
            dataset,
            candidates,
            [(1.0, 0.0)] * len(dataset.queries),
            top_k=1,
            candidate_k=2,
            fusion="rrf",
            reranker=UnknownReranker(),
            rerank_candidates=2,
        )


def test_rerank_client_contract_is_not_confused_with_llm_chat() -> None:
    assert RerankCandidate(key="a", content="doc").content == "doc"


def test_rerank_preflight_runs_before_database_and_embedding(monkeypatch) -> None:
    events: list[str] = []

    class FakeReranker:
        model = "fake-reranker"

        def verify_available(self) -> None:
            events.append("rerank")

        def rerank(self, query, candidates, *, top_n):  # noqa: ANN001, ANN202
            return []

    class FakeEmbedding:
        dimension = 2

        def embed_texts(self, texts):  # noqa: ANN001, ANN202
            events.append("embed_texts")
            return [[1.0, 0.0] for _ in texts]

        def embed_query(self, text):  # noqa: ANN001, ANN202
            events.append("embed_query")
            return [1.0, 0.0]

    monkeypatch.setattr(rerank_experiment, "get_rerank_client", lambda: FakeReranker())
    monkeypatch.setattr(
        rerank_experiment,
        "_git_output",
        lambda arguments: "true" if arguments == ["status", "--porcelain"] else None,
    )
    monkeypatch.setattr(
        rerank_experiment,
        "build_engine",
        lambda database_url: events.append("database") or pytest.fail("不应继续建库"),
    )
    monkeypatch.setattr(rerank_experiment, "get_embedding_client", lambda: FakeEmbedding())
    monkeypatch.setattr(
        rerank_experiment.sys,
        "argv",
        [
            "rerank_experiment.py",
            "--database-url",
            "postgresql+psycopg://user:pass@localhost/aitkp_rerank",
        ],
    )

    with pytest.raises(pytest.fail.Exception):
        rerank_experiment.main()

    assert events == ["rerank", "database"]
