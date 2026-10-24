"""Phase 16 Query Rewrite 对比运行器与 CLI 测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.evaluation.dataset import load_dataset
from app.evaluation.lexical_experiment import BM25Searcher, retrieve_chunks
from app.evaluation.phase12_dataset import load_phase12_dataset
from app.evaluation.query_rewrite_evaluation import (
    evaluate_phase12_understanding,
    evaluate_rewriters,
    evaluate_understanding,
)
from app.query.rewrite import DeterministicQueryRewriter, NoOpQueryRewriter
from app.query.understanding import DeterministicQueryUnderstanding
from scripts import query_rewrite_experiment

DATASET = Path("eval/fixtures/phase8_simulated.json")


def test_rewriter_comparison_preserves_dataset_and_reports_latency() -> None:
    dataset = load_dataset(DATASET)
    results = evaluate_rewriters(
        dataset,
        BM25Searcher(retrieve_chunks(dataset)),
        [NoOpQueryRewriter(), DeterministicQueryRewriter()],
        top_k=10,
    )

    assert [result.name for result in results] == ["noop", "deterministic"]
    assert all(result.mode == "candidate" for result in results)
    assert all(len(result.query_details) == len(dataset.queries) for result in results)
    assert all(result.metrics["rewrite_seconds_p95"] >= 0 for result in results)
    assert all("badcases" in result.metrics for result in results)


def test_rewrite_comparison_requires_rewriter_and_valid_top_k() -> None:
    dataset = load_dataset(DATASET)
    with pytest.raises(ValueError, match="至少"):
        evaluate_rewriters(dataset, BM25Searcher(retrieve_chunks(dataset)), [])
    with pytest.raises(ValueError, match="top_k"):
        evaluate_rewriters(
            dataset,
            BM25Searcher(retrieve_chunks(dataset)),
            [NoOpQueryRewriter()],
            top_k=0,
        )


def test_understanding_report_is_explicitly_exploratory() -> None:
    dataset = load_dataset(DATASET)
    report = evaluate_understanding(dataset, DeterministicQueryUnderstanding())

    assert report["queries"] == len(dataset.queries)
    assert report["strategy"] == "deterministic"
    assert len(report["details"]) == len(dataset.queries)
    assert all("parsed" in item and "filters" in item for item in report["details"])


def test_phase12_understanding_report_preserves_candidate_provenance() -> None:
    dataset = load_phase12_dataset("eval/fixtures/phase12_candidates.json")
    report = evaluate_phase12_understanding(
        dataset,
        DeterministicQueryUnderstanding(),
    )

    assert report["dataset_kind"] == "phase12"
    assert report["mode"] == "candidate"
    assert report["queries"] == 100
    assert report["with_expected_filters"] == 20
    assert report["matched_expected_filters"] == 12
    assert report["expected_filter_match_rate"] == pytest.approx(0.6)
    assert all(item["review_status"] == "pending" for item in report["details"])


def test_phase12_understanding_requires_valid_mode() -> None:
    dataset = load_phase12_dataset("eval/fixtures/phase12_candidates.json")

    with pytest.raises(ValueError, match="mode"):
        evaluate_phase12_understanding(
            dataset,
            DeterministicQueryUnderstanding(),
            mode="production",
        )


def test_phase12_understanding_rejects_pending_rows_in_formal_mode() -> None:
    dataset = load_phase12_dataset("eval/fixtures/phase12_candidates.json")

    with pytest.raises(ValueError, match="confirmed"):
        evaluate_phase12_understanding(
            dataset,
            DeterministicQueryUnderstanding(),
            mode="formal",
        )


def test_cli_writes_candidate_manifest(tmp_path, monkeypatch) -> None:
    output = tmp_path / "phase16.json"
    monkeypatch.setattr(
        query_rewrite_experiment.sys,
        "argv",
        [
            "query_rewrite_experiment.py",
            "--dataset",
            str(DATASET),
            "--output",
            str(output),
            "--top-k",
            "10",
        ],
    )

    query_rewrite_experiment.main()

    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["phase"] == 16
    assert manifest["mode"] == "candidate"
    assert manifest["formal_gold_set"] is False
    assert {result["name"] for result in manifest["results"]} == {
        "noop",
        "deterministic",
    }
    assert manifest["understanding"]["queries"] == len(load_dataset(DATASET).queries)


def test_cli_rejects_formal_mode_until_confirmed_phase12_entry_exists(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        query_rewrite_experiment.sys,
        "argv",
        [
            "query_rewrite_experiment.py",
            "--output",
            str(tmp_path / "formal.json"),
            "--mode",
            "formal",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        query_rewrite_experiment.main()
    assert exc_info.value.code == 2
