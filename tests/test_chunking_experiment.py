"""Phase 6 实验编排的离线测试。"""

from __future__ import annotations

import pytest

from app.evaluation.dataset import load_dataset
from app.retrieval.embedding import FakeEmbeddingClient
from scripts.chunking_experiment import (
    StrategyConfig,
    _strategy_configs,
    build_engine,
    run_experiment,
)


def test_build_engine_rejects_non_experiment_database() -> None:
    with pytest.raises(ValueError, match="chunking"):
        build_engine("postgresql+psycopg://user:secret@127.0.0.1:5432/ai_test_knowledge")


def test_build_engine_rejects_sqlite_without_explicit_opt_in() -> None:
    with pytest.raises(ValueError, match="SQLite"):
        build_engine("sqlite://")


def test_fixed_strategies_use_documented_overlap() -> None:
    configs = {item.name: item for item in _strategy_configs()}

    assert configs["fixed-256"].overlap_chars == 64
    assert configs["fixed-512"].overlap_chars == 64
    assert configs["fixed-800"].overlap_chars == 64
    assert configs["fixed-1200"].overlap_chars == 64


def test_run_experiment_is_reproducible_on_isolated_sqlite() -> None:
    """SQLite 不能做向量检索，但可以验证清空 / 导入 / 切分 / 文档数量的编排。"""
    dataset = load_dataset("eval/fixtures/phase6_simulated.json")
    engine = build_engine("sqlite://", allow_sqlite=True)

    # SQLite 没有 pgvector，不能跑向量检索；这里用空 Query 集合验证索引编排，
    # 并显式断言不会悄悄把 SQLite 当 PostgreSQL 来算召回。
    dataset = dataset.model_copy(update={"queries": []})
    results = run_experiment(
        engine,
        dataset,
        embedding=FakeEmbeddingClient(dimension=1024),
        strategies=[
            StrategyConfig("fixed-256", "fixed", max_chars=256),
            StrategyConfig("entity", "entity"),
        ],
    )

    assert [result.strategy for result in results] == ["fixed-256", "entity"]
    # 清空后重新导入；固定窗口会合并相邻句子，实体级保持“一实体 / 一标题 Section”。
    assert results[0].chunk_count == 30
    assert results[1].chunk_count == 46
    assert results[1].chunk_count > len(dataset.testcases) + len(dataset.defects)
    assert results[0].metrics["answerable_queries"] == 0


def test_dataset_queries_are_not_tuned_to_chunk_ids() -> None:
    dataset = load_dataset("eval/fixtures/phase6_simulated.json")
    for query in dataset.queries:
        for label in query.relevant:
            assert not label.evidence.startswith("chunk-")
