"""Phase 13 报告与 manifest 的一致性检查。"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPORT = Path("docs/RETRIEVAL_EVALUATION.md")
MANIFEST = Path("results/retrieval-phase13.json")


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _overall_rows() -> dict[str, list[float]]:
    rows: dict[str, list[float]] = {}
    pattern = re.compile(
        r"^\| `(?P<name>[^`]+)` \| (?P<r5>[0-9.]+) \| (?P<p5>[0-9.]+) \| "
        r"(?P<mrr>[0-9.]+) \| (?P<n5>[0-9.]+) \| (?P<bad>[0-9]+) \|$"
    )
    for line in REPORT.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            rows[match.group("name")] = [
                float(match.group("r5")),
                float(match.group("p5")),
                float(match.group("mrr")),
                float(match.group("n5")),
                float(match.group("bad")),
            ]
    return rows


def test_overall_report_matches_manifest() -> None:
    manifest = _manifest()
    expected = {
        result["name"]: [
            round(result["metrics"]["recall@5"], 3),
            round(result["metrics"]["precision@5"], 3),
            round(result["metrics"]["mrr"], 3),
            round(result["metrics"]["ndcg@5"], 3),
            result["metrics"]["badcase_count"],
        ]
        for result in manifest["results"]
    }

    assert _overall_rows() == expected


def test_report_has_the_same_dataset_and_mode() -> None:
    manifest = _manifest()
    report = REPORT.read_text(encoding="utf-8")

    assert manifest["phase"] == 13
    assert manifest["mode"] == "candidate"
    assert manifest["formal_gold_set"] is False
    assert manifest["dataset"]["sha256"] in report
    assert str(manifest["dataset"]["queries"]) in report
    assert "探索性" in report
    assert "不是正式 Gold Set" in report


def test_every_result_has_a_stable_hash() -> None:
    manifest = _manifest()

    assert all(len(result["result_sha256"]) == 64 for result in manifest["results"])
    assert len({result["result_sha256"] for result in manifest["results"]}) == len(
        manifest["results"]
    )
