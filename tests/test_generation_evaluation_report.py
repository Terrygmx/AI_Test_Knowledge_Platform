"""Phase 14 报告与 manifest 的一致性检查。"""

from __future__ import annotations

import json
from pathlib import Path

MANIFEST = Path("results/generation-phase14.json")
REPORT = Path("docs/GENERATION_EVALUATION.md")


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_phase14_manifest_is_explicitly_candidate_and_simulated() -> None:
    manifest = _manifest()

    assert manifest["phase"] == 14
    assert manifest["mode"] == "candidate"
    assert manifest["formal_gold_set"] is False
    assert manifest["evaluator"] == "deterministic"
    assert manifest["response_source"] == "simulated"
    assert manifest["dataset"]["review_statuses"]["pending"] == manifest["dataset"]["cases"]
    assert manifest["dataset"]["sha256"] in REPORT.read_text(encoding="utf-8")
    assert manifest["dataset"]["hash_scope"] == "evaluated_cases"


def test_report_overall_metrics_match_manifest() -> None:
    manifest = _manifest()
    report = REPORT.read_text(encoding="utf-8")
    metrics = manifest["metrics"]

    for key in (
        "context_recall",
        "context_precision",
        "faithfulness",
        "answer_relevancy",
        "citation_accuracy",
        "refusal_correct",
    ):
        assert f"{metrics[key]:.3f}" in report
    assert f"| `{metrics['cases']}` |" in report
    assert f"| `{metrics['badcase_count']}` |" in report


def test_each_badcase_is_documented_and_attributed() -> None:
    manifest = _manifest()
    report = REPORT.read_text(encoding="utf-8")
    expected = {case["query_id"]: case["badcase"] for case in manifest["badcases"]}

    assert expected == {
        "P12-TC-003": "generation_error_unsupported_claim",
        "P12-DF-001": "generation_error_missing_answer_point",
        "P12-DOC-002": "generation_error_citation",
        "P12-DOC-006": "retrieval_error",
        "P12-UN-002": "generation_error_false_answer",
    }
    assert manifest["badcases"] == [
        case for case in manifest["cases"] if case["badcase"] is not None
    ]
    for query_id, badcase in expected.items():
        assert query_id in report
        assert badcase in report
