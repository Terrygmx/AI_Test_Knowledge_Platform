"""Phase 12 Evaluation Dataset 的候选 / 人工确认门槛与证据校验。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.evaluation.dataset import load_dataset
from app.evaluation.phase12_dataset import (
    CATEGORY_COUNTS,
    Phase12Category,
    Phase12Dataset,
    Phase12DatasetError,
    Phase12Query,
    ReviewStatus,
    category_counts,
    load_phase12_dataset,
    validate_phase12_dataset,
)
from scripts.phase12_candidates import build_candidate_dataset

DATASET = Path("eval/fixtures/phase12_candidates.json")
CORPUS = Path("eval/fixtures/phase6_simulated.json")


def test_candidate_fixture_has_exact_planned_distribution() -> None:
    dataset = load_phase12_dataset(DATASET)

    assert len(dataset.queries) == 100
    assert category_counts(dataset) == CATEGORY_COUNTS
    assert category_counts(dataset) == {
        Phase12Category.TESTCASE_SEARCH: 30,
        Phase12Category.DEFECT_SEARCH: 20,
        Phase12Category.DOCUMENT_QA: 20,
        Phase12Category.CROSS_VERSION: 10,
        Phase12Category.MULTI_CONDITION: 10,
        Phase12Category.UNANSWERABLE: 10,
    }
    assert sum(query.answerable for query in dataset.queries) == 90
    assert sum(not query.answerable for query in dataset.queries) == 10
    assert all(query.review_status is ReviewStatus.PENDING for query in dataset.queries)
    assert len(dataset.dataset_sha256) == 64


def test_candidate_generator_payload_has_unique_object_fields() -> None:
    import json

    payload = build_candidate_dataset(CORPUS)
    serialized = json.dumps(payload, ensure_ascii=False)
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    for testcase in corpus["testcases"]:
        assert f'{{"case_id": "{testcase["case_id"]}", "case_id"' not in serialized

    for query in payload["queries"]:
        if query["answerable"]:
            assert query["query"] != query["expected_answer_points"][0]


def test_candidate_generator_is_deterministic_except_generation_time() -> None:
    left = build_candidate_dataset(CORPUS)
    right = build_candidate_dataset(CORPUS)

    left.pop("generated_at")
    right.pop("generated_at")
    assert left == right


def test_loader_prefers_explicit_corpus_over_relative_fallback(tmp_path) -> None:
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    dataset_path = tmp_path / "phase12_candidates.json"
    dataset_path.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")
    missing_corpus = tmp_path / "missing-corpus.json"
    missing_corpus.write_text("{}", encoding="utf-8")
    dataset["corpus_dataset"] = missing_corpus.name

    loaded = load_phase12_dataset(
        dataset_path,
        corpus=load_dataset(CORPUS),
    )

    assert len(loaded.queries) == 100


def test_formal_mode_rejects_pending_candidates() -> None:
    with pytest.raises(Phase12DatasetError, match="confirmed"):
        load_phase12_dataset(DATASET, mode="formal")


def test_cli_formal_mode_prints_brief_error(tmp_path, monkeypatch, capsys) -> None:
    from scripts import phase12_candidates

    monkeypatch.setattr(
        phase12_candidates.sys,
        "argv",
        [
            "phase12_candidates.py",
            "--check",
            "--mode",
            "formal",
            "--dataset",
            str(DATASET),
            "--corpus",
            str(CORPUS),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        phase12_candidates.main()

    assert exc_info.value.code == 1
    output = capsys.readouterr().err
    assert "formal 校验失败" in output
    assert "仍有 100 条未确认" in output
    assert "Traceback" not in output


def test_confirmed_query_requires_reviewer_and_timezone() -> None:
    valid_gold = {
        "gold_documents": [
            {
                "source_type": "testcase",
                "source_id": "SHOP-LOGIN-001",
                "evidence": "服务端创建新的会话",
            }
        ],
        "gold_chunks": [
            {
                "source_type": "testcase",
                "source_id": "SHOP-LOGIN-001",
                "chunk_index": 0,
                "evidence": "服务端创建新的会话",
            }
        ],
        "expected_answer_points": ["创建新会话"],
    }
    with pytest.raises(ValidationError, match="reviewed_by"):
        Phase12Query(
            query_id="Q",
            query="支付超时怎么办",
            category="testcase_search",
            expected_intent="定位支付用例",
            generated_by="ai",
            review_status="confirmed",
            **valid_gold,
        )

    with pytest.raises(ValidationError, match="时区"):
        Phase12Query(
            query_id="Q",
            query="支付超时怎么办",
            category="testcase_search",
            expected_intent="定位支付用例",
            generated_by="ai",
            review_status="confirmed",
            reviewed_by="Terry",
            review_notes="证据充分",
            reviewed_at=datetime(2026, 10, 20),
            **valid_gold,
        )


def test_confirmed_query_requires_terry_and_review_notes() -> None:
    valid_gold = {
        "gold_documents": [
            {
                "source_type": "testcase",
                "source_id": "SHOP-LOGIN-001",
                "evidence": "服务端创建新的会话",
            }
        ],
        "gold_chunks": [
            {
                "source_type": "testcase",
                "source_id": "SHOP-LOGIN-001",
                "chunk_index": 0,
                "evidence": "服务端创建新的会话",
            }
        ],
        "expected_answer_points": ["创建新会话"],
    }
    common = dict(
        query_id="Q",
        query="支付超时怎么办",
        category="testcase_search",
        expected_intent="定位支付用例",
        generated_by="ai",
        review_status="confirmed",
        reviewed_at=datetime(2026, 10, 20, tzinfo=UTC),
        **valid_gold,
    )

    with pytest.raises(ValidationError, match="Terry"):
        Phase12Query(reviewed_by="reviewer", review_notes="证据充分", **common)
    with pytest.raises(ValidationError, match="review_notes"):
        Phase12Query(reviewed_by="Terry", **common)
    with pytest.raises(ValidationError, match="review_notes"):
        Phase12Query(reviewed_by="Terry", review_notes="   ", **common)


def test_pending_query_rejects_review_notes() -> None:
    payload = json.loads(DATASET.read_text(encoding="utf-8"))["queries"][0]
    payload["review_notes"] = "未审阅"

    with pytest.raises(ValidationError, match="pending 候选不能带审阅备注"):
        Phase12Query.model_validate(payload)


def test_unanswerable_query_must_not_have_gold_or_filters() -> None:
    with pytest.raises(ValidationError, match="answerable=false"):
        Phase12Query(
            query_id="Q",
            query="支持比特币吗",
            category="unanswerable",
            expected_intent="识别不支持能力",
            generated_by="ai",
        )

    with pytest.raises(ValidationError, match="不应标注 Gold"):
        Phase12Query(
            query_id="Q",
            query="支持比特币吗",
            category="unanswerable",
            expected_intent="识别不支持能力",
            generated_by="ai",
            answerable=False,
            gold_documents=[
                {
                    "source_type": "document",
                    "source_id": "SHOP-DOC-001",
                    "evidence": "支付",
                }
            ],
        )


def test_loader_rejects_chunk_evidence_from_wrong_chunk() -> None:
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    query = payload["queries"][0]
    query["gold_chunks"][0]["chunk_index"] = 999

    with pytest.raises(Phase12DatasetError, match="不存在的 Chunk"):
        validate_phase12_dataset(
            Phase12Dataset.model_validate(payload),
            load_dataset(CORPUS),
        )


def test_loader_rejects_evidence_missing_from_source_text() -> None:
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    payload["queries"][0]["gold_documents"][0]["evidence"] = "语料中不存在的证据"
    dataset = Phase12Dataset.model_validate(payload)

    with pytest.raises(Phase12DatasetError, match="文档证据不在来源正文中"):
        validate_phase12_dataset(dataset, load_dataset(CORPUS))


def test_special_categories_require_expected_filters() -> None:
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    for query in payload["queries"]:
        if query["category"] in {"cross_version", "multi_condition"}:
            query["expected_filters"] = {}
    dataset = Phase12Dataset.model_validate(payload)

    with pytest.raises(Phase12DatasetError, match="必须标注 expected_filters"):
        validate_phase12_dataset(dataset, load_dataset(CORPUS))


def test_loader_rejects_filters_that_exclude_gold_chunk() -> None:
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    payload["queries"][0]["expected_filters"] = {"module": "payment"}
    dataset = Phase12Dataset.model_validate(payload)

    with pytest.raises(Phase12DatasetError, match="expected_filters 会过滤掉 Gold Chunk"):
        validate_phase12_dataset(dataset, load_dataset(CORPUS))


def test_formal_mode_accepts_fully_reviewed_dataset() -> None:
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    reviewed_at = datetime.now(UTC).isoformat()
    for query in payload["queries"]:
        query["review_status"] = "confirmed"
        query["reviewed_by"] = "Terry"
        query["review_notes"] = "证据充分"
        query["reviewed_at"] = reviewed_at
    dataset = Phase12Dataset.model_validate(payload)

    validate_phase12_dataset(dataset, load_dataset(CORPUS), mode="formal")
