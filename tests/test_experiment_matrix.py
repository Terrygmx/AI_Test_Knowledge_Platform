"""Phase 27 实验矩阵聚合测试。"""

from __future__ import annotations

import pytest

from app.evaluation.experiment_matrix import build_experiment_matrix


def test_matrix_aggregates_existing_candidate_results() -> None:
    matrix = build_experiment_matrix(
        retrieval="results/retrieval-phase13.json",
        hybrid="results/hybrid-phase9.json",
        query_rewrite="results/query-rewrite-phase16.json",
    )

    experiments = {row["experiment"] for row in matrix["rows"]}
    assert {"bm25", "hybrid", "query_rewrite"} <= experiments
    assert "vector" in matrix["missing_experiments"]
    assert "hybrid_rerank" in matrix["missing_experiments"]
    assert "final" in matrix["missing_experiments"]

    bm25 = next(row for row in matrix["rows"] if row["experiment"] == "bm25")
    assert bm25["recall@5"] == pytest.approx(0.944, abs=0.001)
    assert bm25["mode"] == "candidate"
    assert bm25["formal_gold_set"] is False

    rewrite = next(row for row in matrix["rows"] if row["experiment"] == "query_rewrite")
    assert rewrite["recall@5"] == pytest.approx(0.944, abs=0.001)


def test_matrix_requires_at_least_one_manifest() -> None:
    with pytest.raises(ValueError, match="至少"):
        build_experiment_matrix()


def test_matrix_notes_candidate_boundary() -> None:
    matrix = build_experiment_matrix(retrieval="results/retrieval-phase13.json")

    assert "candidate" in matrix["note"]
    assert matrix["rows"]
