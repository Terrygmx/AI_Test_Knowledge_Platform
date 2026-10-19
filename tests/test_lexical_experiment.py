"""Phase 8 词法检索实验的离线测试。"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

import pytest

from app.evaluation.dataset import load_dataset
from app.evaluation.lexical_experiment import (
    ILikeSearcher,
    RetrievedChunk,
    demonstrate_baseline_failure,
    evaluate_lexical,
    evaluate_searcher,
    retrieve_chunks,
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


def test_offline_chunks_keep_real_chunk_indices_for_hybrid_identity() -> None:
    chunks = retrieve_chunks(load_dataset(DATASET))
    document_indices = [
        (item.source_id, item.chunk_index)
        for item in chunks
        if item.source_type == "document"
    ]

    assert len(chunks) == 47
    assert document_indices
    assert len(set(document_indices)) == len(document_indices)
    assert any(index > 0 for _, index in document_indices)


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
        top_k=10,
    )

    assert manifest["phase"] == 8
    assert manifest["algorithm"] == {
        "name": "bm25",
        "k1": 1.2,
        "b": 0.75,
        "tokenizer": "unicode-whole+cjk-unigram-bigram",
    }
    assert manifest["dataset"]["sha256"] == dataset.dataset_sha256
    assert manifest["git_commit"]
    assert isinstance(manifest["git_dirty"], bool)
    assert manifest["top_k"] == 10
    assert manifest["k_values"] == [5, 10]


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
            top_k=10,
        )


@pytest.mark.parametrize(
    ("top_k", "expected_k_values", "expected_recall_metrics"),
    [
        (1, (1,), {"recall@1"}),
        (5, (5,), {"recall@5"}),
        (7, (5, 7), {"recall@5", "recall@7"}),
        (10, (5, 10), {"recall@5", "recall@10"}),
    ],
)
def test_metrics_follow_top_k(
    top_k: int,
    expected_k_values: tuple[int, ...],
    expected_recall_metrics: set[str],
) -> None:
    dataset = load_dataset(DATASET)
    result = evaluate_searcher(
        ILikeSearcher(retrieve_chunks(dataset)),
        dataset,
        name="ilike",
        top_k=top_k,
    )

    recall_metrics = {
        key
        for key in result.metrics
        if key.startswith("recall@") and not key.endswith(("_hits", "_labels"))
    }
    assert result.k_values == expected_k_values
    assert recall_metrics == expected_recall_metrics


def test_cli_top_k_one_only_emits_recall_at_one(tmp_path, monkeypatch, capsys) -> None:
    from scripts import lexical_experiment

    output = tmp_path / "lexical-top1.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "lexical_experiment.py",
            "--dataset",
            DATASET,
            "--output",
            str(output),
            "--top-k",
            "1",
        ],
    )

    lexical_experiment.main()

    import json

    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["top_k"] == 1
    assert manifest["k_values"] == [1]
    assert all("recall@5" not in result["metrics"] for result in manifest["results"])
    assert all("recall@10" not in result["metrics"] for result in manifest["results"])
    stdout = capsys.readouterr().out
    assert "R@1=" in stdout
    assert "R@5=" not in stdout
    assert "R@10=" not in stdout


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
            r"(?:\*\*)?(?P<bm25>[0-9.]+)(?:\*\*)? \| "
            r"(?:\*\*)?(?P<mrr>[0-9.]+)(?:\*\*)? \|$",
            report,
            re.MULTILINE,
        )
        if match.group("name") != "类别"
    }
    assert set(category_rows) == (
        set(results["bm25"]["categories"]) - {"unanswerable"}
    )
    for category, values in category_rows.items():
        assert values[0] == round(results["ilike"]["categories"][category]["recall@5"], 3)
        assert values[1] == round(results["bm25"]["categories"][category]["recall@5"], 3)
        assert values[2] == round(results["bm25"]["categories"][category]["mrr"], 3)

    assert manifest["baseline_failure"]["ilike"]["matches"] == 0
    assert manifest["baseline_failure"]["bm25"]["top_source_id"] == "BUG-1234"
