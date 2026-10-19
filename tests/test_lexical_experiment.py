"""Phase 8 词法检索实验的离线测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.evaluation.dataset import load_dataset
from app.evaluation.lexical_experiment import (
    RetrievedChunk,
    demonstrate_baseline_failure,
    evaluate_lexical,
)
from app.retrieval.lexical import BM25Index, LexicalDocument
from scripts.lexical_experiment import _manifest

DATASET = "eval/fixtures/phase8_simulated.json"


def test_phase8_dataset_covers_identifier_and_semantic_queries() -> None:
    dataset = load_dataset(DATASET)

    categories = {query.category for query in dataset.queries}
    assert len(dataset.queries) >= 20
    assert {
        "exact_identifier",
        "exact_version",
        "exact_priority",
        "semantic_paraphrase",
    } <= categories
    assert sum(query.answerable for query in dataset.queries) > 0
    assert sum(not query.answerable for query in dataset.queries) > 0


def test_evaluation_reports_per_category_and_negative_queries() -> None:
    dataset = load_dataset(DATASET)
    documents = [
        LexicalDocument(
            source_type="defect",
            source_id="BUG-1234",
            content="P0 BUG-1234 login-api v1.3 登录接口超时",
        )
    ]

    result = evaluate_lexical(dataset, BM25Index(documents))

    assert result.metrics["answerable_queries"] == len(
        [query for query in dataset.queries if query.answerable]
    )
    assert result.metrics["unanswerable_queries"] >= 1
    assert set(result.categories) == {
        "exact_identifier",
        "exact_version",
        "exact_priority",
        "semantic_paraphrase",
        "unanswerable",
    }


def test_baseline_failure_is_explicit_and_evidence_based() -> None:
    documents = [
        RetrievedChunk(
            source_type="defect",
            source_id="BUG-1234",
            content="P0 BUG-1234 login-api v1.3 登录接口超时",
        ),
        RetrievedChunk(
            source_type="testcase",
            source_id="TC-1",
            content="登录接口验证码校验",
        ),
    ]

    result = demonstrate_baseline_failure(documents)

    assert result["query"] == "P0 BUG-1234 login-api v1.3"
    assert result["ilike"]["matches"] == 1
    assert result["bm25"]["matches"] >= 1
    assert result["bm25"]["top_source_id"] == "BUG-1234"


def test_manifest_records_algorithm_parameters_and_dataset_hash() -> None:
    dataset = load_dataset(DATASET)
    now = datetime.now(UTC)

    manifest = _manifest(
        dataset,
        [],
        dataset_path=DATASET,
        started_at=now,
        finished_at=now,
        command=["python", "scripts/lexical_experiment.py"],
        k1=1.2,
        b=0.75,
    )

    assert manifest["phase"] == 8
    assert manifest["algorithm"] == {
        "name": "bm25",
        "k1": 1.2,
        "b": 0.75,
        "tokenizer": "ascii-whole+cjk-unigram-bigram",
    }
    assert manifest["dataset"]["sha256"] == dataset.dataset_sha256
    assert manifest["git_commit"]
    assert isinstance(manifest["git_dirty"], bool)


def test_manifest_rejects_non_positive_parameters() -> None:
    dataset = load_dataset(DATASET)
    now = datetime.now(UTC)

    with pytest.raises(ValueError, match="k1"):
        _manifest(
            dataset,
            [],
            dataset_path=DATASET,
            started_at=now,
            finished_at=now,
            command=[],
            k1=0,
            b=0.75,
        )


def test_report_metrics_match_manifest() -> None:
    """报告中的总体与分类数字必须能追溯到同一次原始结果。"""
    import json
    import re
    from pathlib import Path

    manifest = json.loads(Path("results/lexical-phase8.json").read_text(encoding="utf-8"))
    report = Path("docs/LEXICAL_SEARCH_EXPERIMENT.md").read_text(encoding="utf-8")
    results = {item["name"]: item for item in manifest["results"]}

    overall = re.search(
        r"\| `ILIKE` 整串子串 \| ([0-9.]+) \| ([0-9.]+) \| ([0-9.]+) \|.*\n"
        r"\| BM25 \| \*\*([0-9.]+)\*\* \| \*\*([0-9.]+)\*\* \| \*\*([0-9.]+)\*\* \|",
        report,
    )
    assert overall is not None
    assert [float(value) for value in overall.groups()[:3]] == [
        round(results["ilike"]["metrics"][metric], 3)
        for metric in ("recall@5", "recall@10", "mrr")
    ]
    assert [float(value) for value in overall.groups()[3:]] == [
        round(results["bm25"]["metrics"][metric], 3)
        for metric in ("recall@5", "recall@10", "mrr")
    ]

    category_rows = {
        match.group("name"): [
            float(match.group("ilike")),
            float(match.group("bm25")),
            float(match.group("mrr")),
        ]
        for match in re.finditer(
            r"^\| `(?P<name>[^`]+)` \| (?P<ilike>[0-9.]+) \| "
            r"\*\*(?P<bm25>[0-9.]+)\*\* \| (?:\*\*)?(?P<mrr>[0-9.]+)(?:\*\*)? \|$",
            report,
            re.MULTILINE,
        )
        if match.group("name") != "类别"
    }
    for category, values in category_rows.items():
        assert values[0] == round(results["ilike"]["categories"][category]["recall@5"], 3)
        assert values[1] == round(results["bm25"]["categories"][category]["recall@5"], 3)
        assert values[2] == round(results["bm25"]["categories"][category]["mrr"], 3)

    assert manifest["baseline_failure"]["ilike"]["matches"] == 0
    assert manifest["baseline_failure"]["bm25"]["top_source_id"] == "BUG-1234"
