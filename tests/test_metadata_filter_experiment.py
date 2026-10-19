"""Phase 10 Metadata Filter 实验逻辑测试。"""

from __future__ import annotations

import pytest

from app.evaluation.dataset import EvaluationQuery, load_dataset
from app.evaluation.metadata_filter_experiment import (
    FilterCandidate,
    evaluate_metadata_filters,
    matches_metadata,
)
from scripts.metadata_filter_experiment import _filter_sql, _manifest

DATASET = "eval/fixtures/phase10_simulated.json"


def _candidate(
    source_id: str,
    *,
    module: str,
    tags: list[str],
    content: str = "登录接口超时",
    embedding: tuple[float, ...] = (1.0, 0.0),
) -> FilterCandidate:
    return FilterCandidate(
        source_type="testcase",
        source_id=source_id,
        chunk_index=0,
        content=content,
        embedding=embedding,
        meta={"module": module, "tags": tags},
    )


def _query(filters: dict[str, object]) -> EvaluationQuery:
    return EvaluationQuery(
        query_id="Q",
        query="登录接口超时",
        relevant=[],
        answerable=False,
        expected_filters=filters,
    )


class TestMetadataFilterSemantics:
    def test_scalar_and_array_filters_use_and(self) -> None:
        meta = {"module": "login", "tags": ["smoke", "security"]}
        assert matches_metadata(meta, {"module": "login", "tags": ["smoke"]})
        assert not matches_metadata(meta, {"module": "payment", "tags": ["smoke"]})
        assert not matches_metadata(meta, {"tags": ["pagination"]})

    def test_missing_or_wrong_type_does_not_match(self) -> None:
        assert not matches_metadata({}, {"module": "login"})
        assert not matches_metadata({"tags": "smoke"}, {"tags": ["smoke"]})


class TestMetadataFilterExperiment:
    def test_prefilter_does_not_starve_after_limit(self) -> None:
        dataset = load_dataset(DATASET)
        candidates = [
            _candidate("DISTRACTOR", module="payment", tags=["smoke"], embedding=(1.0, 0.0)),
            _candidate("TARGET", module="login", tags=["smoke"], embedding=(0.0, 1.0)),
        ]
        query = EvaluationQuery(
            query_id="Q",
            query="登录接口超时",
            relevant=[
                {
                    "source_type": "testcase",
                    "source_id": "TARGET",
                    "evidence": "登录接口超时",
                }
            ],
            expected_filters={"module": "login"},
        )
        dataset = dataset.model_copy(update={"queries": [query]})

        result = evaluate_metadata_filters(
            dataset,
            candidates,
            [[1.0, 0.0]],
            top_k=1,
            candidate_k=1,
            strategy="vector",
        )
        variants = {item.name: item for item in result.variants}

        assert variants["pre"].result_counts == [1]
        assert variants["pre"].candidate_counts_after == [1]
        assert variants["pre"].metrics["recall@1"] == 1.0
        # 后过滤先截断 Top-1，目标被候选饥饿掉；这是必须显式暴露的失败模式。
        assert variants["post"].result_counts == [0]

    def test_post_filter_reports_candidate_starvation(self) -> None:
        """后过滤只取最终 Top-K，因此可能返回少于 K 条甚至空结果。"""
        query = EvaluationQuery(
            query_id="Q",
            query="登录接口超时",
            relevant=[
                {
                    "source_type": "testcase",
                    "source_id": "TARGET",
                    "evidence": "登录接口超时",
                }
            ],
            expected_filters={"module": "login"},
        )
        dataset = load_dataset(DATASET).model_copy(
            update={"queries": [query]}
        )
        candidates = [
            _candidate("D1", module="payment", tags=["smoke"], embedding=(1.0, 0.0)),
            _candidate("D2", module="payment", tags=["smoke"], embedding=(0.99, 0.01)),
            _candidate("TARGET", module="login", tags=["smoke"], embedding=(0.0, 1.0)),
        ]

        result = evaluate_metadata_filters(
            dataset,
            candidates,
            [[1.0, 0.0]],
            top_k=1,
            candidate_k=2,
            strategy="vector",
        )
        variants = {item.name: item for item in result.variants}

        assert variants["pre"].metrics["recall@1"] == 1.0
        assert variants["post"].metrics["recall@1"] == 0.0
        assert variants["post"].empty_result_queries == 1

    def test_filter_is_not_applied_when_query_has_no_filters(self) -> None:
        dataset = load_dataset(DATASET)
        query = _query({})
        dataset = dataset.model_copy(update={"queries": [query]})
        candidates = [_candidate("A", module="login", tags=["smoke"])]

        result = evaluate_metadata_filters(
            dataset,
            candidates,
            [[1.0, 0.0]],
            top_k=1,
            candidate_k=1,
            strategy="vector",
        )

        assert all(item.candidate_counts_after == [1] for item in result.variants)
        assert all(item.gold_filtered_out_queries == 0 for item in result.variants)

    def test_invalid_query_embedding_count_is_rejected(self) -> None:
        dataset = load_dataset(DATASET)
        with pytest.raises(ValueError, match="数量"):
            evaluate_metadata_filters(dataset, [], [])

    def test_candidate_depth_cannot_be_smaller_than_top_k(self) -> None:
        dataset = load_dataset(DATASET)
        with pytest.raises(ValueError, match="candidate_k"):
            evaluate_metadata_filters(
                dataset,
                [],
                [[1.0, 0.0]] * len(dataset.queries),
                top_k=5,
                candidate_k=2,
            )

    def test_gold_filtered_out_is_counted_separately_from_empty_results(self) -> None:
        """过滤条件错误时，结果可能非空但 Gold 证据已被误杀。"""
        query = EvaluationQuery(
            query_id="Q",
            query="登录接口超时",
            relevant=[
                {
                    "source_type": "testcase",
                    "source_id": "TARGET",
                    "evidence": "登录接口超时",
                }
            ],
            expected_filters={"module": "payment"},
        )
        dataset = load_dataset(DATASET).model_copy(
            update={"queries": [query]}
        )
        candidates = [
            _candidate("TARGET", module="login", tags=["smoke"]),
            _candidate("OTHER", module="payment", tags=["smoke"]),
        ]

        result = evaluate_metadata_filters(
            dataset,
            candidates,
            [[1.0, 0.0]],
            top_k=1,
            candidate_k=1,
            strategy="vector",
        )
        variants = {item.name: item for item in result.variants}

        assert variants["pre"].gold_filtered_out_queries == 1
        assert variants["pre"].empty_result_queries == 0

    def test_manifest_marks_offline_placeholder_vectors(self) -> None:
        dataset = load_dataset(DATASET).model_copy(
            update={"queries": [_query({"module": "login"})]}
        )
        candidates = [_candidate("A", module="login", tags=["smoke"])]
        result = evaluate_metadata_filters(
            dataset,
            candidates,
            [[1.0, 0.0]],
            top_k=1,
            candidate_k=1,
        )

        manifest = _manifest(
            dataset,
            result,
            dataset_path="fixture.json",
            started_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            finished_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            command=["python", "script.py"],
            result_path="result.json",
            top_k=1,
            candidate_k=1,
            strategy="rrf",
            rrf_k=60,
            embedding_model="offline-placeholder",
            embedding_dimension=1024,
            embedding_batch_size=32,
            embedding_timeout_seconds=60,
            embedding_max_retries=2,
            candidate_sha256="0" * 64,
            database_url="offline",
            mode="offline",
        )

        assert manifest["mode"] == "offline"
        assert manifest["environment"]["embedding_model"] == "offline-placeholder"
        assert manifest["retrieval"]["filter_semantics"]["arrays"] == "contains-any"

    def test_real_sql_filter_uses_contains_any_on_postgresql(self, engine) -> None:
        if engine.dialect.name != "postgresql":
            pytest.skip("PostgreSQL only")
        from sqlalchemy import Column, Integer, MetaData, String, Table, Text

        from app.models.types import JSONMap

        table = Table(
            "filter_sql_contract",
            MetaData(),
            Column("id", Integer, primary_key=True),
            Column("source_type", String(32)),
            Column("source_id", String(64)),
            Column("chunk_index", Integer),
            Column("content", Text),
            Column("metadata", JSONMap),
        )
        stmt = _filter_sql(
            table,
            {"tags": ["smoke", "security"]},
            dialect_name="postgresql",
        )
        compiled = str(stmt.compile(dialect=engine.dialect))
        assert compiled.count("@>") == 2
        assert " OR " in compiled
