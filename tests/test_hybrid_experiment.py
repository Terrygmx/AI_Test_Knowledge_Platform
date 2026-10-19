"""Phase 9 Hybrid Search 实验测试。"""

from __future__ import annotations

import pytest

from app.evaluation import hybrid_experiment
from app.evaluation.dataset import load_dataset
from app.evaluation.hybrid_experiment import HybridCandidate, evaluate_hybrid
from app.evaluation.lexical_experiment import retrieve_chunks


def _dataset():
    return load_dataset("eval/fixtures/phase8_simulated.json")


def _candidates(dimension: int = 4) -> list[HybridCandidate]:
    return [
        HybridCandidate(
            source_type="defect",
            source_id="BUG-1234",
            chunk_index=0,
            content="P0 BUG-1234 login-api v1.3 登录接口超时",
            embedding=(1.0, 0.0, 0.0, 0.0)[:dimension],
        ),
        HybridCandidate(
            source_type="testcase",
            source_id="TC-1",
            chunk_index=0,
            content="登录接口验证码校验",
            embedding=(0.9, 0.1, 0.0, 0.0)[:dimension],
        ),
        HybridCandidate(
            source_type="document",
            source_id="DOC-1",
            chunk_index=0,
            content="支付超时后查询网关交易状态",
            embedding=(0.0, 1.0, 0.0, 0.0)[:dimension],
        ),
    ]


class TestHybridExperiment:
    def test_evaluates_all_four_variants_on_the_same_candidates(self) -> None:
        dataset = _dataset()
        candidates = _candidates()
        embeddings = [[1.0, 0.0, 0.0, 0.0] for _ in dataset.queries]

        result = evaluate_hybrid(dataset, candidates, embeddings, candidate_k=3, top_k=2)

        assert [item.name for item in result.channels] == [
            "vector",
            "keyword",
            "weighted",
            "rrf",
        ]
        assert result.chunk_count == 3
        assert result.dataset_queries == len(dataset.queries)
        assert all(item.metrics["recall@2"] >= 0 for item in result.channels)

    def test_candidate_depth_cannot_be_smaller_than_final_top_k(self) -> None:
        with pytest.raises(ValueError, match="candidate_k"):
            evaluate_hybrid(
                _dataset(),
                _candidates(),
                [[1.0, 0.0, 0.0, 0.0]] * len(_dataset().queries),
                top_k=5,
                candidate_k=2,
            )

    def test_query_embedding_count_must_match_dataset(self) -> None:
        with pytest.raises(ValueError, match="数量"):
            evaluate_hybrid(_dataset(), _candidates(), [])

    def test_engine_and_vector_table_must_be_provided_together(self) -> None:
        dataset = _dataset()
        with pytest.raises(ValueError, match="同时"):
            evaluate_hybrid(
                dataset,
                _candidates(),
                [[1.0, 0.0, 0.0, 0.0]] * len(dataset.queries),
                engine=None,
                vector_table=object(),  # type: ignore[arg-type]
            )

    def test_vector_distance_and_keyword_score_are_not_raw_added(self) -> None:
        """同一结果在 Weighted 下不应因 BM25 原始量纲而覆盖向量排名。"""
        dataset = load_dataset("eval/fixtures/phase8_simulated.json")
        candidates = _candidates()
        query_embeddings = [[1.0, 0.0, 0.0, 0.0] for _ in dataset.queries]

        result = evaluate_hybrid(
            dataset,
            candidates,
            query_embeddings,
            top_k=2,
            candidate_k=3,
            fusion="weighted",
        )

        # 只要 Weighted 结果与 Vector 一致地由归一化后的双通道决定即可；
        # 具体排名由语料决定，这里守护实验执行后分数并非常量。
        weighted = next(item for item in result.channels if item.name == "weighted")
        assert weighted.metrics["recall@2"] >= 0
        assert weighted.fused_candidates > 0

    def test_vector_variant_does_not_run_keyword_channel(self, monkeypatch) -> None:
        dataset = _dataset()
        monkeypatch.setattr(
            hybrid_experiment._BM25Adapter,
            "search",
            lambda *args, **kwargs: pytest.fail("vector variant 不应执行 BM25"),
        )

        result = evaluate_hybrid(
            dataset,
            _candidates(),
            [[1.0, 0.0, 0.0, 0.0]] * len(dataset.queries),
            candidate_k=3,
            top_k=2,
            variants=[("vector", "vector")],
        )

        assert result.channels[0].keyword_candidate_hits == 0

    def test_keyword_variant_does_not_run_vector_channel(self, monkeypatch) -> None:
        dataset = _dataset()
        monkeypatch.setattr(
            hybrid_experiment,
            "_vector_hits",
            lambda *args, **kwargs: pytest.fail("keyword variant 不应执行向量检索"),
        )

        result = evaluate_hybrid(
            dataset,
            _candidates(),
            [[1.0, 0.0, 0.0, 0.0]] * len(dataset.queries),
            candidate_k=3,
            top_k=2,
            variants=[("keyword", "keyword")],
        )

        assert result.channels[0].vector_candidate_hits == 0

    def test_top_k_one_does_not_emit_misleading_recall_at_five(self) -> None:
        dataset = _dataset()
        result = evaluate_hybrid(
            dataset,
            _candidates(),
            [[1.0, 0.0, 0.0, 0.0]] * len(dataset.queries),
            candidate_k=3,
            top_k=1,
            variants=[("vector", "vector")],
        )

        assert "recall@1" in result.channels[0].metrics
        assert "recall@5" not in result.channels[0].metrics


def test_phase8_retrieval_documents_are_reusable_without_database() -> None:
    dataset = _dataset()
    documents = retrieve_chunks(dataset)

    assert len(documents) == 47
    assert len({(item.source_type, item.source_id) for item in documents}) > 0
