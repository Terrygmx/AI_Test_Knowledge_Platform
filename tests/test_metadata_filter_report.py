"""Phase 10 报告与 manifest 的一致性检查。"""

from __future__ import annotations

import json
from pathlib import Path

REPORT = Path("docs/METADATA_FILTER.md")
MANIFEST = Path("results/metadata-filter-phase10.json")


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _result_rows() -> dict[str, dict[str, str]]:
    lines = REPORT.read_text(encoding="utf-8").splitlines()
    start = lines.index("## 5. 结果")
    header = next(
        index
        for index in range(start, len(lines))
        if lines[index].startswith("| 变体 |")
    )
    rows: dict[str, dict[str, str]] = {}
    for line in lines[header + 2 :]:
        if not line.startswith("|"):
            break
        values = [value.strip() for value in line.strip().strip("|").split("|")]
        if len(values) != 5:
            raise AssertionError(f"表格列数不一致：{line}")
        rows[values[0]] = {
            "recall": values[1],
            "mrr": values[2],
            "empty": values[3],
            "gold_filtered": values[4],
        }
    return rows


def test_report_metrics_match_manifest() -> None:
    manifest = _manifest()
    results = {item["name"]: item for item in manifest["results"]}
    rows = _result_rows()

    assert set(rows) == {"`none`", "`pre`", "`post`", "`during`"}
    for name, row in rows.items():
        result = results[name.strip("`")]
        assert float(row["recall"]) == round(result["metrics"]["recall@5"], 3)
        assert float(row["mrr"]) == round(result["metrics"]["mrr"], 3)
        assert int(row["empty"]) == result["empty_result_queries"]
        assert int(row["gold_filtered"]) == result["gold_filtered_out_queries"]


def test_manifest_records_filter_scope_and_ann_boundary() -> None:
    manifest = _manifest()
    retrieval = manifest["retrieval"]

    assert manifest["phase"] == 10
    assert manifest["git_dirty"] is False
    assert manifest["dataset"]["sha256"] == (
        "34bccafb4551e47d3998756cf8c007023e1b28d9f0d157c452d95485f5e00726"
    )
    assert manifest["dataset"]["queries"] == 22
    assert manifest["dataset"]["answerable"] == 18
    assert manifest["dataset"]["unanswerable"] == 4
    assert manifest["chunks"]["count"] == 47
    assert retrieval["ann_index"] is None
    assert retrieval["variants"] == ["none", "pre", "post", "during"]
    assert "not an independent HNSW ANN run" in retrieval["variant_execution"]["during"]
    assert retrieval["filter_semantics"] == {
        "scalar": "exact",
        "arrays": "contains-any",
        "combine": "and",
        "missing": "no-match",
    }


def test_report_declares_dataset_and_small_sample_limits() -> None:
    manifest = _manifest()
    report = REPORT.read_text(encoding="utf-8")

    assert manifest["dataset"]["sha256"] in report
    assert "22 条 Query / 47 个 Chunk" in report
    assert "候选饥饿" in report
    assert "没有 HNSW" in report
    assert "不能作为 ANN、数据库 P95 或 API P95 结论" in report
