"""Phase 11 Hybrid + Rerank 实验的排序与延迟边界。"""

from __future__ import annotations

import pytest

from app.evaluation.dataset import load_dataset
from app.evaluation.hybrid_experiment import HybridCandidate, evaluate_hybrid
from app.evaluation.lexical_experiment import retrieve_chunks
from app.rerank.service import RerankCandidate, RerankedCandidate, RerankError

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

    assert [channel.name for channel in result.channels] == ["rrf"]
    assert reranker.calls
    assert result.rerank_seconds >= 0.0


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
