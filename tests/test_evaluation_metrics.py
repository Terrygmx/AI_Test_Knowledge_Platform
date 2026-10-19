"""Phase 6 Gold Set 契约与检索指标测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.evaluation.dataset import ChunkingDataset, EvaluationQuery, load_dataset
from app.evaluation.metrics import RetrievalEvaluation, percentile
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
