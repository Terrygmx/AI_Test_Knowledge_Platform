"""Phase 15 Query Understanding 候选报告一致性测试。"""

from __future__ import annotations

import json
from pathlib import Path

from scripts import query_understanding_experiment

MANIFEST = Path("results/query-understanding-phase12.json")
REPORT = Path("docs/QUERY_UNDERSTANDING_REWRITE.md")


def test_phase15_candidate_report_matches_manifest_and_is_not_formal() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    report = REPORT.read_text(encoding="utf-8")
    understanding = manifest["understanding"]

    assert manifest["phase"] == 15
    assert manifest["mode"] == "candidate"
    assert manifest["formal_gold_set"] is False
    assert manifest["dataset"]["sha256"] in report
    assert manifest["dataset"]["review_statuses"] == {"pending": 100}
    assert understanding["queries"] == 100
    assert str(understanding["with_specific_intent"]) in report
    assert str(understanding["with_filters"]) in report
    assert (
        f"{understanding['matched_expected_filters']}/"
        f"{understanding['with_expected_filters']}"
    ) in report
    assert "不是意图准确率" in report


def test_phase15_cli_writes_candidate_manifest(tmp_path, monkeypatch) -> None:
    output = tmp_path / "phase15.json"
    monkeypatch.setattr(
        query_understanding_experiment.sys,
        "argv",
        [
            "query_understanding_experiment.py",
            "--dataset",
            "eval/fixtures/phase12_candidates.json",
            "--output",
            str(output),
        ],
    )

    query_understanding_experiment.main()

    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["phase"] == 15
    assert manifest["mode"] == "candidate"
    assert manifest["formal_gold_set"] is False
    assert manifest["dataset"]["review_statuses"] == {"pending": 100}
