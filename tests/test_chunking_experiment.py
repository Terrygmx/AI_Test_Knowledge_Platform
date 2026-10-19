"""Phase 6 实验编排的离线测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.evaluation.dataset import load_dataset
from app.ingestion.markdown import MarkdownParser
from app.retrieval.embedding import FakeEmbeddingClient
from scripts.chunking_experiment import (
    StrategyConfig,
    StrategyResult,
    _document_chunks,
    _manifest,
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

    assert list(configs) == [
        "fixed-256",
        "fixed-512",
        "fixed-800",
        "fixed-1200",
        "semantic",
        "entity+heading",
    ]
    assert configs["fixed-256"].overlap_chars == 64
    assert configs["fixed-512"].overlap_chars == 64
    assert configs["fixed-800"].overlap_chars == 64
    assert configs["fixed-1200"].overlap_chars == 64
    assert configs["semantic"].min_chars == 40


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
            StrategyConfig(
                "semantic",
                "semantic",
                max_chars=800,
                threshold=0.55,
                min_chars=40,
            ),
        ],
    )

    assert [result.strategy for result in results] == [
        "fixed-256",
        "entity",
        "semantic",
    ]
    # 清空后重新导入；固定窗口会合并相邻句子，实体级保持“一实体 / 一标题 Section”。
    assert results[0].chunk_count == 30
    assert results[1].chunk_count == 46
    assert results[1].chunk_count > len(dataset.testcases) + len(dataset.defects)
    assert results[0].metrics["answerable_queries"] == 0
    assert results[0].embedding_calls_during_chunking == 0
    assert results[2].embedding_calls_during_chunking > 0
    assert results[0].embedding_texts_during_index == results[0].chunk_count
    assert results[2].chunking_seconds >= 0
    assert results[2].total_setup_seconds == pytest.approx(
        results[2].chunking_seconds + results[2].index_seconds
    )


def test_dataset_queries_are_not_tuned_to_chunk_ids() -> None:
    dataset = load_dataset("eval/fixtures/phase6_simulated.json")
    for query in dataset.queries:
        for label in query.relevant:
            assert not label.evidence.startswith("chunk-")


def test_document_chunks_keep_source_spans_and_heading_paths() -> None:
    dataset = load_dataset("eval/fixtures/phase6_simulated.json")
    entity_items = _document_chunks(
        dataset,
        StrategyConfig("entity+heading", "entity"),
        embedding=FakeEmbeddingClient(dimension=1024),
    )

    assert entity_items
    documents = {item.doc_id: item for item in dataset.documents}
    for item in entity_items:
        document = documents[item.source_id]
        start = item.metadata["span_start"]
        end = item.metadata["span_end"]
        assert isinstance(start, int)
        assert isinstance(end, int)
        assert document.content[start:end] == item.content
        assert item.metadata["heading_path"]

    fixed_items = _document_chunks(
        dataset,
        StrategyConfig("fixed-256", "fixed", max_chars=256, overlap_chars=64),
        embedding=FakeEmbeddingClient(dimension=1024),
    )
    assert any(item.metadata["overlap_prefix"] for item in fixed_items)
    for item in fixed_items:
        document = documents[item.source_id]
        start = item.metadata["span_start"]
        end = item.metadata["span_end"]
        assert document.content[start:end]
        assert _squash(item.content).endswith(_squash(document.content[start:end]))


def test_semantic_document_chunks_stay_at_section_granularity() -> None:
    dataset = load_dataset("eval/fixtures/phase6_simulated.json")
    config = {item.name: item for item in _strategy_configs()}["semantic"]
    items = _document_chunks(
        dataset,
        config,
        embedding=FakeEmbeddingClient(dimension=1024),
    )

    section_count = sum(
        len(MarkdownParser.parse(document.content).sections)
        for document in dataset.documents
    )
    assert len(items) <= section_count


def test_manifest_records_reproducibility_fields() -> None:
    dataset = load_dataset("eval/fixtures/phase6_simulated.json")
    config = _strategy_configs()[0]
    result = StrategyResult(
        strategy=config.name,
        chunk_count=1,
        average_chars=10.0,
        median_chars=10.0,
        max_chars=10,
        chunking_seconds=0.1,
        index_seconds=0.2,
        total_setup_seconds=0.3,
        query_p95_seconds=0.01,
        embedding_calls_during_chunking=0,
        embedding_texts_during_chunking=0,
        embedding_calls_during_index=1,
        embedding_texts_during_index=1,
        metrics={"recall@5": 1.0},
    )
    now = datetime.now(UTC)

    manifest = _manifest(
        dataset,
        [result],
        [config],
        database_url="postgresql+psycopg://user:password@localhost/aitkp_chunking",
        dataset_path="eval/fixtures/phase6_simulated.json",
        started_at=now,
        finished_at=now,
        command=["python", "scripts/chunking_experiment.py"],
    )

    assert manifest["git_commit"]
    assert isinstance(manifest["git_dirty"], bool)
    assert manifest["environment"]["embedding_base_url"]
    assert manifest["environment"]["database_name"] == "aitkp_chunking"
    assert manifest["strategy_configs"][0]["name"] == "fixed-256"
    assert manifest["strategies"][0]["total_setup_seconds"] == 0.3


def test_manifest_redacts_database_password() -> None:
    dataset = load_dataset("eval/fixtures/phase6_simulated.json")
    config = _strategy_configs()[0]
    now = datetime.now(UTC)
    result = StrategyResult(
        strategy=config.name,
        chunk_count=0,
        average_chars=0.0,
        median_chars=0.0,
        max_chars=0,
        chunking_seconds=0.0,
        index_seconds=0.0,
        total_setup_seconds=0.0,
        query_p95_seconds=0.0,
        embedding_calls_during_chunking=0,
        embedding_texts_during_chunking=0,
        embedding_calls_during_index=0,
        embedding_texts_during_index=0,
        metrics={},
    )

    manifest = _manifest(
        dataset,
        [result],
        [config],
        database_url="postgresql+psycopg://user:secret@localhost/aitkp_chunking",
        dataset_path="eval/fixtures/phase6_simulated.json",
        started_at=now,
        finished_at=now,
        command=[
            "python",
            "script",
            "postgresql+psycopg://user:secret@localhost/db",
        ],
    )

    assert manifest["command"][-1] == "postgresql+psycopg://user:***@localhost/db"
    assert "secret" not in str(manifest["command"])


def _squash(text: str) -> str:
    return "".join(text.split())
