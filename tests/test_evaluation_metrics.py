"""Phase 6 Gold Set 契约与检索指标测试。"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.evaluation.dataset import ChunkingDataset, EvaluationQuery, load_dataset
from app.evaluation.metrics import (
    RetrievalEvaluation,
    mrr,
    ndcg_at_k,
    percentile,
    precision_at_k,
    query_metric_details,
    recall_at_k,
    reciprocal_rank,
)
from app.models import KnowledgeChunk

DATASET = Path("eval/fixtures/phase6_simulated.json")


def _chunk(source_type: str, source_id: str, content: str) -> KnowledgeChunk:
    return KnowledgeChunk(
        source_type=source_type,
        source_id=source_id,
        chunk_index=0,
        content=content,
        content_hash="0" * 64,
        meta={},
    )


def test_simulated_dataset_is_valid_and_strategy_independent() -> None:
    dataset = load_dataset(DATASET)

    assert dataset.version == "2026-10-20"
    assert len(dataset.testcases) == 12
    assert len(dataset.defects) == 5
    assert len(dataset.requirements) == 5
    assert len(dataset.documents) == 3
    assert len(dataset.queries) == 40
    assert sum(query.answerable for query in dataset.queries) == 34
    assert sum(not query.answerable for query in dataset.queries) == 6
    assert len(dataset.dataset_sha256) == 64
    assert dataset.sources


def test_dataset_rejects_unknown_evidence_source() -> None:
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    payload["queries"][0]["relevant"][0]["source_id"] = "MISSING-DOC"

    with pytest.raises(ValidationError, match="不存在的来源"):
        ChunkingDataset.model_validate(payload)


def test_answerable_query_requires_evidence() -> None:
    with pytest.raises(ValidationError, match="至少标注一条"):
        EvaluationQuery(query_id="Q", query="支付失败怎么办", relevant=[])


def test_unanswerable_query_rejects_evidence() -> None:
    with pytest.raises(ValidationError, match="不应标注"):
        EvaluationQuery(
            query_id="Q",
            query="不支持的问题",
            relevant=[
                {
                    "source_type": "document",
                    "source_id": "DOC",
                    "evidence": "某段证据",
                }
            ],
            answerable=False,
        )


class TestRetrievalMetrics:
    def test_recall_and_mrr_use_source_and_evidence(self) -> None:
        query = EvaluationQuery(
            query_id="Q1",
            query="支付超时",
            relevant=[
                {
                    "source_type": "document",
                    "source_id": "DOC-1",
                    "evidence": "使用原幂等键查询",
                },
                {
                    "source_type": "testcase",
                    "source_id": "TC-1",
                    "evidence": "不得重复扣款",
                },
            ],
        )
        results = [
            _chunk("document", "DOC-1", "无关内容"),
            _chunk("document", "DOC-1", "超时后使用原幂等键查询交易状态"),
            _chunk("testcase", "TC-1", "超时重试不得重复扣款"),
        ]
        evaluator = RetrievalEvaluation(k_values=(1, 2, 5))

        evaluator.add(query, results)

        assert evaluator.recall[1] == 0.0
        assert evaluator.recall[2] == 0.5
        assert evaluator.recall[5] == 1.0
        assert evaluator.mrr == 0.5

    def test_wrong_source_id_does_not_count_as_hit(self) -> None:
        query = EvaluationQuery(
            query_id="Q1",
            query="登录锁定",
            relevant=[
                {
                    "source_type": "requirement",
                    "source_id": "REQ-1",
                    "evidence": "锁定三十分钟",
                }
            ],
        )
        evaluator = RetrievalEvaluation(k_values=(5,))

        evaluator.add(
            query,
            [_chunk("testcase", "REQ-1", "锁定三十分钟")],
        )

        assert evaluator.recall[5] == 0.0
        assert evaluator.mrr == 0.0

    def test_normalization_handles_width_whitespace_and_case(self) -> None:
        query = EvaluationQuery(
            query_id="Q1",
            query="登录",
            relevant=[
                {
                    "source_type": "document",
                    "source_id": "DOC-1",
                    "evidence": "Use  Stable Idempotency-Key",
                }
            ],
        )
        evaluator = RetrievalEvaluation(k_values=(1,))

        evaluator.add(
            query,
            [_chunk("document", "DOC-1", "use stable   idempotency-key")],
        )

        assert evaluator.recall[1] == 1.0

    def test_unanswerable_queries_track_false_positives(self) -> None:
        query = EvaluationQuery(
            query_id="Q1",
            query="系统支持扫码登录吗",
            answerable=False,
        )
        evaluator = RetrievalEvaluation(k_values=(5,))

        evaluator.add(query, [_chunk("document", "DOC-1", "任意结果")])

        assert evaluator.answerable_queries == 0
        assert evaluator.unanswerable_queries == 1
        summary = evaluator.summary()
        assert summary["unanswerable_false_positive_rate"] == 1.0

    def test_unanswerable_queries_record_top1_distance(self) -> None:
        query = EvaluationQuery(
            query_id="Q1",
            query="系统支持扫码登录吗",
            answerable=False,
        )
        evaluator = RetrievalEvaluation(k_values=(5,))

        evaluator.add(
            query,
            [_chunk("document", "DOC-1", "任意结果")],
            distances=[0.64],
        )

        summary = evaluator.summary()
        assert summary["unanswerable_top1_distance_min"] == 0.64
        assert summary["unanswerable_top1_distance_median"] == 0.64

    def test_empty_results_produce_zero_metrics(self) -> None:
        query = EvaluationQuery(
            query_id="Q1",
            query="支付",
            relevant=[
                {
                    "source_type": "document",
                    "source_id": "DOC-1",
                    "evidence": "支付",
                }
            ],
        )
        evaluator = RetrievalEvaluation()

        evaluator.add(query, [])

        assert evaluator.mrr == 0.0
        assert set(evaluator.recall.values()) == {0.0}


def test_percentile_uses_nearest_rank() -> None:
    assert percentile([1, 2, 3, 4], 0.5) == 2
    assert percentile([1, 2, 3, 4], 0.95) == 4
    assert percentile([], 0.95) == 0.0


class TestStandaloneRetrievalMetrics:
    def test_recall_and_precision_use_the_same_top_k(self) -> None:
        retrieved = ["A", "B", "C", "D"]
        gold = ["B", "D", "MISSING"]

        assert recall_at_k(retrieved, gold, 3) == pytest.approx(1 / 3)
        assert precision_at_k(retrieved, gold, 3) == pytest.approx(1 / 3)
        assert recall_at_k(retrieved, gold, 4) == pytest.approx(2 / 3)
        assert precision_at_k(retrieved, gold, 4) == pytest.approx(2 / 4)

    def test_precision_uses_actual_result_count_when_underfilled(self) -> None:
        assert precision_at_k(["A"], ["A", "B"], 5) == 1.0
        assert precision_at_k([], ["A"], 5) == 0.0

    def test_recall_precision_and_ndcg_validate_k(self) -> None:
        for function in (recall_at_k, precision_at_k, ndcg_at_k):
            with pytest.raises(ValueError, match="正整数"):
                function(["A"], ["A"], 0)

    def test_empty_gold_does_not_divide_by_zero(self) -> None:
        assert recall_at_k(["A"], [], 5) == 0.0
        assert precision_at_k(["A"], [], 5) == 0.0
        assert ndcg_at_k(["A"], [], 5) == 0.0
        assert reciprocal_rank(["A"], []) == 0.0
        assert mrr([["A"]], [[]]) == 0.0

    def test_reciprocal_rank_and_mrr(self) -> None:
        assert reciprocal_rank(["X", "A", "B"], ["A", "B"]) == 0.5
        assert reciprocal_rank(["X"], ["A"]) == 0.0
        assert mrr(
            [["X", "A"], ["B"], ["X"]],
            [["A"], ["B"], ["A"]],
        ) == pytest.approx((0.5 + 1.0 + 0.0) / 3)

    def test_mrr_rejects_mismatched_query_counts(self) -> None:
        with pytest.raises(ValueError, match="Query 数"):
            mrr([["A"]], [["A"], ["B"]])

    def test_ndcg_rewards_earlier_relevant_results(self) -> None:
        early = ndcg_at_k(["A", "X", "B"], ["A", "B"], 3)
        late = ndcg_at_k(["X", "X", "A"], ["A", "B"], 3)

        assert early == pytest.approx((1.0 + 1 / math.log2(4)) / (1.0 + 1 / math.log2(3)))
        assert late < early
        assert ndcg_at_k(["X"], ["A"], 1) == 0.0

    def test_duplicate_gold_labels_are_set_semantics(self) -> None:
        assert recall_at_k(["A"], ["A", "A"], 1) == 1.0
        assert precision_at_k(["A"], ["A", "A"], 1) == 1.0
        assert ndcg_at_k(["A"], ["A", "A"], 1) == 1.0

    def test_duplicate_retrieved_items_cannot_inflate_ndcg(self) -> None:
        assert ndcg_at_k(["A", "A"], ["A"], 2) == 1.0
        assert ndcg_at_k(["A", "A", "B"], ["A", "B"], 3) == pytest.approx(
            ndcg_at_k(["A", "X", "B"], ["A", "B"], 3)
        )
        assert ndcg_at_k(["A", "A", "B"], ["A", "B"], 3) <= 1.0

    def test_query_details_attributes_wrong_source_badcase(self) -> None:
        query = EvaluationQuery(
            query_id="Q",
            query="支付超时",
            relevant=[
                {
                    "source_type": "document",
                    "source_id": "DOC-1",
                    "evidence": "原幂等键",
                }
            ],
        )
        details = query_metric_details(
            query,
            [_chunk("document", "DOC-1", "无关内容")],
            k_values=(1,),
        )

        assert details["badcase"] == "wrong_source"
        assert details["metrics"]["recall@1"] == 0.0

    def test_query_details_marks_unanswerable_false_positive(self) -> None:
        query = EvaluationQuery(
            query_id="Q",
            query="系统支持扫码吗",
            answerable=False,
        )

        details = query_metric_details(
            query,
            [_chunk("document", "DOC-1", "任意结果")],
            k_values=(5,),
        )

        assert details["badcase"] == "unexpected_results"


def test_retrieval_evaluation_exports_precision_ndcg_and_badcases() -> None:
    query = EvaluationQuery(
        query_id="Q",
        query="支付超时",
        relevant=[
            {
                "source_type": "document",
                "source_id": "DOC-1",
                "evidence": "原幂等键",
            },
            {
                "source_type": "testcase",
                "source_id": "TC-1",
                "evidence": "不得重复扣款",
            },
        ],
    )
    evaluator = RetrievalEvaluation(k_values=(1, 2))

    evaluator.add(
        query,
        [
            _chunk("document", "DOC-1", "原幂等键查询交易"),
            _chunk("testcase", "TC-1", "不得重复扣款"),
        ],
    )
    summary = evaluator.summary()

    assert summary["precision@1"] == pytest.approx(1.0)
    assert summary["precision@2"] == pytest.approx(1.0)
    assert summary["ndcg@1"] == pytest.approx(1.0)
    assert summary["ndcg@2"] == pytest.approx(1.0)
    assert summary["badcase_count"] == 0
