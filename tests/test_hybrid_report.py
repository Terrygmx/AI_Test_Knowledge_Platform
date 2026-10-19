"""Phase 9 报告与原始 manifest 的一致性检查。"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPORT = Path("docs/HYBRID_SEARCH_EXPERIMENT.md")
MANIFEST = Path("results/hybrid-phase9.json")
SWEEP = Path("results/hybrid-sweep-phase9.json")


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _sweep() -> dict:
    return json.loads(SWEEP.read_text(encoding="utf-8"))


def _table_rows(
    section_start: str,
    *,
    columns: tuple[str, ...],
) -> list[dict[str, str]]:
    """解析指定标题之后的第一张 Markdown 表。"""
    text = REPORT.read_text(encoding="utf-8")
    start = text.index(section_start)
    next_section = text.find("\n### ", start + len(section_start))
    if next_section < 0:
        next_section = text.find("\n## ", start + len(section_start))
    if next_section < 0:
        next_section = len(text)
    lines = text[start:next_section].splitlines()
    header_index = next(
        index
        for index, line in enumerate(lines)
        if line.startswith("|") and f"| {columns[0]} |" in line
    )
    rows: list[dict[str, str]] = []
    for line in lines[header_index + 2 :]:
        if not line.startswith("|"):
            break
        values = [value.strip() for value in line.strip().strip("|").split("|")]
        if len(values) != len(columns):
            raise AssertionError(f"表格列数不一致：{line}")
        rows.append(dict(zip(columns, values, strict=True)))
    return rows


def _clean_number(value: str) -> float:
    return float(value.replace("*", "").replace("s", ""))


def test_overall_quality_and_latency_match_manifest() -> None:
    manifest = _manifest()
    expected = {
        result["name"]: result
        for result in manifest["results"]
    }
    labels = {
        "Vector": "vector",
        "Keyword": "keyword",
        "Weighted 1:1": "weighted",
        "RRF 1:1, k=60": "rrf",
    }
    rows = _table_rows(
        "### 5.1 总体检索质量",
        columns=(
            "通道",
            "Recall@5",
            "Recall@10",
            "MRR",
            "Query P50",
            "Query P95",
        ),
    )
    assert {row["通道"] for row in rows} == set(labels)

    for row in rows:
        result = expected[labels[row["通道"]]]
        assert _clean_number(row["Recall@5"]) == round(
            float(result["metrics"]["recall@5"]), 3
        )
        assert _clean_number(row["Recall@10"]) == round(
            float(result["metrics"]["recall@10"]), 3
        )
        assert _clean_number(row["MRR"]) == round(float(result["metrics"]["mrr"]), 3)
        assert _clean_number(row["Query P50"]) == float(
            f"{float(result['query_p50_seconds']):.4f}"
        )
        assert _clean_number(row["Query P95"]) == round(
            float(result["query_p95_seconds"]), 4
        )


def test_unanswerable_rate_matches_manifest() -> None:
    manifest = _manifest()
    expected = {
        result["name"]: result["metrics"]["unanswerable_false_positive_rate"]
        for result in manifest["results"]
    }
    labels = {
        "Vector": "vector",
        "Keyword": "keyword",
        "Weighted 1:1": "weighted",
        "RRF 1:1, k=60": "rrf",
    }
    rows = _table_rows(
        "### 5.2 不可回答 Query",
        columns=(
            "通道",
            "误召回率",
            "top1 distance 最小值",
            "top1 distance 中位数",
        ),
    )
    assert {row["通道"] for row in rows} == set(labels)
    for row in rows:
        assert _clean_number(row["误召回率"]) == round(
            float(expected[labels[row["通道"]]]), 3
        )


def test_sweep_tables_match_manifest() -> None:
    sweep = _sweep()
    expected_weighted = {
        (round(item["vector_weight"]), round(item["keyword_weight"])): item
        for item in sweep["results"]
        if item["strategy"] == "weighted"
    }
    weighted_rows = _table_rows(
        "## 6. 参数敏感性",
        columns=("方法", "vector:keyword / k", "Recall@5", "MRR"),
    )
    observed_weighted = 0
    observed_rrf = 0
    for row in weighted_rows:
        if row["方法"] != "Weighted":
            continue
        vector_weight, keyword_weight = (int(item) for item in row["vector:keyword / k"].split(":"))
        item = expected_weighted[(vector_weight, keyword_weight)]
        assert _clean_number(row["Recall@5"]) == round(
            float(item["metrics"]["recall@5"]), 3
        )
        assert _clean_number(row["MRR"]) == round(float(item["metrics"]["mrr"]), 3)
        observed_weighted += 1
    assert observed_weighted == len(expected_weighted)

    expected_rrf = {
        (
            round(item["vector_weight"]),
            round(item["keyword_weight"]),
            item["rrf_k"],
        ): item
        for item in sweep["results"]
        if item["strategy"] == "rrf"
    }
    for row in weighted_rows:
        if row["方法"] != "RRF":
            continue
        weights, raw_k = row["vector:keyword / k"].split(", k=")
        vector_weight, keyword_weight = (int(item) for item in weights.split(":"))
        item = expected_rrf[(vector_weight, keyword_weight, int(raw_k))]
        assert _clean_number(row["Recall@5"]) == round(
            float(item["metrics"]["recall@5"]), 3
        )
        assert _clean_number(row["MRR"]) == round(float(item["metrics"]["mrr"]), 3)
        observed_rrf += 1
    expected_rrf_rows = {key for key in expected_rrf if key[2] == 60}
    assert observed_rrf == len(expected_rrf_rows)

    expected_rrf_k = {
        item["rrf_k"]: item
        for item in sweep["results"]
        if item["strategy"] == "rrf"
        and item["vector_weight"] == 1.0
        and item["keyword_weight"] == 1.0
    }
    k_rows = _table_rows(
        "RRF 的 `k` 在 1:1 下扫描",
        columns=("k", "Recall@5", "MRR"),
    )
    assert {int(row["k"]) for row in k_rows} == set(expected_rrf_k)
    for row in k_rows:
        item = expected_rrf_k[int(row["k"])]
        assert _clean_number(row["Recall@5"]) == round(
            float(item["metrics"]["recall@5"]), 3
        )
        assert _clean_number(row["MRR"]) == round(float(item["metrics"]["mrr"]), 3)


def test_report_uses_the_same_dataset_and_chunk_identity() -> None:
    manifest = _manifest()
    report = REPORT.read_text(encoding="utf-8")

    assert manifest["dataset"]["sha256"] in report
    assert manifest["manifest_schema_version"] == 2
    assert manifest["git_commit_role"] == "generation"
    assert manifest["result_path"] == str(MANIFEST)
    assert manifest["retrieval"]["selection"] == "all"
    assert manifest["retrieval"]["evaluated_variants"] == [
        "vector",
        "keyword",
        "weighted",
        "rrf",
    ]
    assert manifest["retrieval"]["default_fusion"] == "rrf"
    assert manifest["chunks"]["count"] == 47
    assert "24 条 Query" in report
    assert "(source_type, source_id, chunk_index)" in report
    assert re.search(r"RRF.*?k=60", report, re.DOTALL)


def test_report_explains_generation_commit_and_artifact_commit_are_distinct() -> None:
    report = REPORT.read_text(encoding="utf-8")

    assert "生成提交" in report
    assert "产物入库提交" in report
    assert "a73d963" in report
    assert "2c179e0" in report
