"""Phase 13 检索评测运行器、投影与结果契约测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.evaluation.dataset import load_dataset
from app.evaluation.lexical_experiment import BM25Searcher, ILikeSearcher, retrieve_chunks
from app.evaluation.phase12_dataset import (
    load_phase12_dataset,
    phase12_to_chunking_dataset,
)
from app.evaluation.retrieval_evaluation import (
    evaluate_retriever,
    retrieval_result_sha256,
)
from scripts import retrieval_evaluation

PHASE8 = Path("eval/fixtures/phase8_simulated.json")
PHASE12 = Path("eval/fixtures/phase12_candidates.json")
PHASE6 = Path("eval/fixtures/phase6_simulated.json")


def test_evaluation_runner_outputs_precision_ndcg_and_badcases() -> None:
    dataset = load_dataset(PHASE8)
    result = evaluate_retriever(
        dataset,
        BM25Searcher(retrieve_chunks(dataset)),
        name="bm25",
        mode="candidate",
        top_k=10,
    )

    assert result.metrics["recall@5"] > 0
    assert "precision@5" in result.metrics
    assert "ndcg@5" in result.metrics
    assert result.metrics["badcase_count"] >= 1
    assert len(result.query_details) == len(dataset.queries)
    assert result.query_p50_seconds >= 0
    assert result.query_p95_seconds >= 0


def test_ilike_baseline_is_measurable_with_same_contract() -> None:
    dataset = load_dataset(PHASE8)
    result = evaluate_retriever(
        dataset,
        ILikeSearcher(retrieve_chunks(dataset)),
        name="ilike",
        mode="candidate",
        top_k=10,
    )

    assert result.metrics["recall@5"] < 0.5
    assert result.metrics["badcase_count"] > 0


def test_result_hash_is_stable_and_ignores_latency() -> None:
    dataset = load_dataset(PHASE8)
    left = evaluate_retriever(
        dataset,
        BM25Searcher(retrieve_chunks(dataset)),
        name="bm25",
        mode="candidate",
        top_k=10,
    )
    right = evaluate_retriever(
        dataset,
        BM25Searcher(retrieve_chunks(dataset)),
        name="bm25",
        mode="candidate",
        top_k=10,
    )
    right.query_latencies = [999.0]
    right.query_p95_seconds = 999.0

    assert retrieval_result_sha256(left) == retrieval_result_sha256(right)


def test_phase12_projection_preserves_gold_and_corpus_sources() -> None:
    phase12 = load_phase12_dataset(PHASE12)
    corpus = load_dataset(PHASE6)
    projected = phase12_to_chunking_dataset(phase12, corpus)

    assert len(projected.queries) == 100
    assert projected.testcases == corpus.testcases
    assert projected.queries[0].relevant[0].evidence == (
        phase12.queries[0].gold_documents[0].evidence
    )
    assert projected.queries[0].expected_filters == phase12.queries[0].expected_filters


def test_phase12_projection_requires_corpus_to_keep_sources() -> None:
    phase12 = load_phase12_dataset(PHASE12)
    with pytest.raises(ValueError, match="必须传入语料"):
        phase12_to_chunking_dataset(phase12)


def test_phase12_candidate_results_are_explicitly_exploratory() -> None:
    phase12 = load_phase12_dataset(PHASE12)
    corpus = load_dataset(PHASE6)
    projected = phase12_to_chunking_dataset(phase12, corpus)
    result = evaluate_retriever(
        projected,
        BM25Searcher(retrieve_chunks(projected)),
        name="bm25",
        mode="candidate",
        top_k=10,
    )

    assert result.mode == "candidate"
    assert result.metrics["answerable_queries"] == 90
    assert result.metrics["unanswerable_queries"] == 10
    assert "badcases" in result.metrics


@pytest.mark.parametrize("mode", ["unknown", "formal-preview"])
def test_invalid_evaluation_mode_is_rejected(mode: str) -> None:
    dataset = load_dataset(PHASE8)
    with pytest.raises(ValueError, match="mode"):
        evaluate_retriever(
            dataset,
            ILikeSearcher(retrieve_chunks(dataset)),
            name="ilike",
            mode=mode,
            top_k=10,
        )


def test_cli_candidate_mode_writes_manifest_with_exploratory_flag(
    tmp_path,
    monkeypatch,
) -> None:
    output = tmp_path / "phase13.json"
    monkeypatch.setattr(
        retrieval_evaluation.sys,
        "argv",
        [
            "retrieval_evaluation.py",
            "--phase12-dataset",
            str(PHASE12),
            "--corpus",
            str(PHASE6),
            "--output",
            str(output),
            "--mode",
            "candidate",
        ],
    )

    retrieval_evaluation.main()

    manifest = __import__("json").loads(output.read_text(encoding="utf-8"))
    assert manifest["phase"] == 13
    assert manifest["mode"] == "candidate"
    assert manifest["formal_gold_set"] is False
    assert {item["name"] for item in manifest["results"]} == {"ilike", "bm25"}
    assert all(item["metrics"]["badcase_count"] >= 0 for item in manifest["results"])


def test_cli_formal_mode_rejects_pending_phase12_without_output(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    output = tmp_path / "formal.json"
    monkeypatch.setattr(
        retrieval_evaluation.sys,
        "argv",
        [
            "retrieval_evaluation.py",
            "--phase12-dataset",
            str(PHASE12),
            "--corpus",
            str(PHASE6),
            "--output",
            str(output),
            "--mode",
            "formal",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        retrieval_evaluation.main()

    assert exc_info.value.code == 1
    assert not output.exists()
    assert "仍有 100 条未确认" in capsys.readouterr().err
