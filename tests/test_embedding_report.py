"""Phase 7 报告与原始 manifest 的一致性检查。"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPORT = Path("docs/EMBEDDING_EXPERIMENT.md")
MANIFEST = Path("results/embedding-phase7.json")


def _report_quality_rows() -> dict[str, list[float]]:
    rows: dict[str, list[float]] = {}
    pattern = re.compile(
        r"^\| `(?P<name>[^`]+)` \| \d+ \| \d+ \| "
        r"(?P<r5>\*?\*?[0-9.]+\*?\*?) \| "
        r"(?P<r10>\*?\*?[0-9.]+\*?\*?) \| "
        r"(?P<mrr>\*?\*?[0-9.]+\*?\*?) \|$"
    )
    for line in REPORT.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            rows[match.group("name")] = [
                float(match.group("r5").replace("*", "")),
                float(match.group("r10").replace("*", "")),
                float(match.group("mrr").replace("*", "")),
            ]
    return rows


def _report_unanswerable_rows() -> dict[str, list[float]]:
    rows: dict[str, list[float]] = {}
    pattern = re.compile(
        r"^\| `(?P<name>[^`]+)` \| (?P<rate>[0-9.]+) \| "
        r"(?P<min_distance>[0-9.]+) \| (?P<median_distance>[0-9.]+) \|$"
    )
    for line in REPORT.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            rows[match.group("name")] = [
                float(match.group("rate")),
                float(match.group("min_distance")),
                float(match.group("median_distance")),
            ]
    return rows


def _report_latency_rows() -> dict[str, list[float]]:
    rows: dict[str, list[float]] = {}
    pattern = re.compile(
        r"^\| `(?P<name>[^`]+)` \| (?P<embedding>[0-9.]+)s \| "
        r"(?P<write>[0-9.]+)s \| (?P<index>[0-9.]+)s \| "
        r"(?P<q50>[0-9.]+)s / (?P<q95>[0-9.]+)s \| "
        r"(?P<search>[0-9.]+)s \| (?P<total>[0-9.]+)s \|$"
    )
    for line in REPORT.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            rows[match.group("name")] = [
                float(match.group(field))
                for field in ("embedding", "write", "index", "q50", "q95", "search", "total")
            ]
    return rows


def _manifest_values(manifest: dict) -> dict[str, list[float]]:
    return {
        result["name"]: [
            round(float(result["content_embedding_seconds"]), 3),
            round(float(result["write_seconds"]), 3),
            round(float(result["index_seconds"]), 3),
            round(float(result["query_embedding_p50_seconds"]), 3),
            round(float(result["query_embedding_p95_seconds"]), 3),
            round(float(result["search_p95_seconds"]), 3),
            round(float(result["query_total_p95_seconds"]), 3),
        ]
        for result in manifest["results"]
    }


def test_reported_quality_matches_manifest() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    reported = _report_quality_rows()
    expected = {
        result["name"]: [
            round(float(result["metrics"]["recall@5"]), 3),
            round(float(result["metrics"]["recall@10"]), 3),
            round(float(result["metrics"]["mrr"]), 3),
        ]
        for result in manifest["results"]
    }

    assert reported == expected


def test_report_has_one_row_per_manifest_model() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert set(_report_quality_rows()) == {item["name"] for item in manifest["results"]}


def test_reported_unanswerable_metrics_match_manifest() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    expected = {
        result["name"]: [
            round(float(result["metrics"]["unanswerable_false_positive_rate"]), 3),
            round(float(result["metrics"]["unanswerable_top1_distance_min"]), 3),
            round(float(result["metrics"]["unanswerable_top1_distance_median"]), 3),
        ]
        for result in manifest["results"]
    }

    assert _report_unanswerable_rows() == expected


def test_reported_latency_matches_manifest() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert _report_latency_rows() == _manifest_values(manifest)
