"""Phase 24 Dashboard 聚合契约测试。"""

from __future__ import annotations

import pytest

from app.evaluation.dashboard import build_dashboard


def test_dashboard_aggregates_all_manifests() -> None:
    summary = build_dashboard(
        retrieval="results/retrieval-phase13.json",
        generation="results/generation-phase14.json",
        dataset="results/query-understanding-phase12.json",
    )

    retrieval = summary["retrieval"]
    assert retrieval["kind"] == "retrieval"
    assert retrieval["mode"] == "candidate"
    assert retrieval["formal_gold_set"] is False
    assert {item["name"] for item in retrieval["results"]} == {"ilike", "bm25"}
    assert all(item["recall@5"] is not None for item in retrieval["results"])

    generation = summary["generation"]
    assert generation["kind"] == "generation"
    assert generation["cases"] == 10
    assert generation["faithfulness"] is not None

    dataset = summary["dataset"]
    assert dataset["kind"] == "dataset"
    assert dataset["queries"] == 100
    assert dataset["review_statuses"] == {"pending": 100}
    assert dataset["expected_filter_match_rate"] == pytest.approx(0.6)


def test_dashboard_allows_partial_manifests() -> None:
    summary = build_dashboard(dataset="results/query-understanding-phase12.json")

    assert summary["retrieval"] is None
    assert summary["generation"] is None
    assert summary["dataset"] is not None


def test_dashboard_requires_at_least_one_manifest() -> None:
    with pytest.raises(ValueError, match="至少"):
        build_dashboard()
