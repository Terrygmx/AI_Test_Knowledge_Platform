"""Phase 15/16 报告与 manifest 一致性测试。"""

from __future__ import annotations

import json
from pathlib import Path

MANIFEST = Path("results/query-rewrite-phase16.json")
REPORT = Path("docs/QUERY_UNDERSTANDING_REWRITE.md")


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_phase16_manifest_is_explicitly_candidate() -> None:
    manifest = _manifest()
    report = REPORT.read_text(encoding="utf-8")

    assert manifest["phase"] == 16
    assert manifest["mode"] == "candidate"
    assert manifest["formal_gold_set"] is False
    assert manifest["dataset"]["sha256"] in report
    assert str(manifest["dataset"]["queries"]) in report
    assert "不是业务准确率" in report


def test_phase16_report_metrics_match_manifest() -> None:
    manifest = _manifest()
    report = REPORT.read_text(encoding="utf-8")
    results = {item["name"]: item["metrics"] for item in manifest["results"]}

    for name in ("noop", "deterministic"):
        assert f"{results[name]['recall@5']:.3f}" in report
        assert f"{results[name]['mrr']:.3f}" in report
    assert str(manifest["understanding"]["queries"]) in report
    assert str(manifest["understanding"]["with_specific_intent"]) in report
    assert str(manifest["understanding"]["with_filters"]) in report
