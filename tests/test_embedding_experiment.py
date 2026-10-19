"""Phase 7 Embedding 对比实验的离线测试。"""

from __future__ import annotations

import pytest

from app.evaluation.dataset import load_dataset
from app.evaluation.embedding_experiment import (
    EmbeddingConfig,
    EmbeddingResult,
    build_engine,
    chunk_length_summary,
    parse_model_specs,
    run_embedding_experiment,
)
from app.retrieval.embedding import FakeEmbeddingClient
from app.schemas.knowledge_chunk import KnowledgeChunkCreate
from scripts.chunking_experiment import StrategyConfig, _document_chunks
from scripts.embedding_experiment import _manifest


def test_parse_model_specs_rejects_ambiguous_configuration() -> None:
    with pytest.raises(ValueError, match="model:dimension"):
        parse_model_specs("bge-m3")
    with pytest.raises(ValueError, match="重复"):
        parse_model_specs("bge-m3:1024,bge-m3:768")
    with pytest.raises(ValueError, match="大于 0"):
        parse_model_specs("bge-m3:0")


def test_build_engine_requires_embedding_database() -> None:
    with pytest.raises(ValueError, match="embedding"):
        build_engine("postgresql+psycopg://user:pass@localhost/ai_test_knowledge")
    with pytest.raises(ValueError, match="SQLite"):
        build_engine("sqlite://")


def test_parse_model_specs_preserves_order_and_dimensions() -> None:
    configs = parse_model_specs(
        "bge-m3-mlx-4bit:1024, embeddinggemma-2-oQ8e:768,all-MiniLM-L6-v2:384"
    )

    assert [(item.name, item.dimension) for item in configs] == [
        ("bge-m3-mlx-4bit", 1024),
        ("embeddinggemma-2-oQ8e", 768),
        ("all-MiniLM-L6-v2", 384),
    ]


def test_chunk_length_summary_is_stable() -> None:
    chunks = [
        KnowledgeChunkCreate(
            source_type="testcase",
            source_id="A",
            content="1234567890",
        ),
        KnowledgeChunkCreate(
            source_type="testcase",
            source_id="B",
            content="12345678901234567890",
        ),
    ]

    assert chunk_length_summary(chunks) == {
        "chunk_count": 2,
        "average_chars": 15.0,
        "median_chars": 15.0,
        "max_chars": 20,
    }


def test_phase7_reuses_phase6_entity_heading_baseline() -> None:
    dataset = load_dataset("eval/fixtures/phase6_simulated.json")
    embedding = FakeEmbeddingClient(dimension=1024)
    documents = _document_chunks(
        dataset,
        StrategyConfig("entity+heading", "entity"),
        embedding=embedding,
    )

    assert len(documents) == 24
    assert all(item.metadata["heading_path"] for item in documents)


def test_run_embedding_experiment_requires_matching_clients() -> None:
    with pytest.raises(ValueError, match="数量"):
        run_embedding_experiment(
            build_engine("sqlite://", allow_sqlite=True),
            load_dataset("eval/fixtures/phase6_simulated.json"),
            [],
            [EmbeddingConfig("a", "a", 8)],
            [],
        )


def test_manifest_redacts_database_password() -> None:
    from datetime import UTC, datetime

    dataset = load_dataset("eval/fixtures/phase6_simulated.json")
    config = EmbeddingConfig("bge", "bge-m3", 1024)
    result = EmbeddingResult(
        name="bge",
        model="bge-m3",
        dimension=1024,
        table_name="phase7_embedding_bge_1024",
        chunk_count=46,
        warmup_seconds=0.1,
        content_embedding_seconds=2.0,
        write_seconds=0.2,
        index_seconds=2.2,
        query_embedding_p50_seconds=0.01,
        query_embedding_p95_seconds=0.02,
        search_p50_seconds=0.01,
        search_p95_seconds=0.02,
        query_total_p50_seconds=0.02,
        query_total_p95_seconds=0.04,
        metrics={"recall@5": 1.0},
    )
    now = datetime.now(UTC)

    manifest = _manifest(
        dataset,
        [result],
        [config],
        [
            KnowledgeChunkCreate(
                source_type="testcase",
                source_id="A",
                content="内容",
            )
        ],
        database_url="postgresql+psycopg://user:secret@localhost/aitkp_embedding",
        dataset_path="eval/fixtures/phase6_simulated.json",
        started_at=now,
        finished_at=now,
        command=[
            "python",
            "script",
            "postgresql+psycopg://user:secret@localhost/aitkp_embedding",
        ],
    )

    assert manifest["phase"] == 7
    assert manifest["chunking_baseline"]["strategy"] == "entity+heading"
    assert manifest["command"][-1] == (
        "postgresql+psycopg://user:***@localhost/aitkp_embedding"
    )
    assert "secret" not in str(manifest["command"])


def test_postgres_uses_separate_vector_tables_per_dimension(
    engine,
    db_session,
) -> None:
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL + pgvector only")

    from app.evaluation.embedding_experiment import evaluate_embedding

    dataset = load_dataset("eval/fixtures/phase6_simulated.json").model_copy(
        update={"queries": []}
    )
    chunks = [
        KnowledgeChunkCreate(
            source_type="testcase",
            source_id="A",
            content="登录失败",
        )
    ]
    configs = [
        EmbeddingConfig("small", "small", 8),
        EmbeddingConfig("large", "large", 16),
    ]

    results = [
        evaluate_embedding(
            engine,
            dataset,
            chunks,
            config,
            FakeEmbeddingClient(dimension=config.dimension),
        )
        for config in configs
    ]

    assert results[0].table_name != results[1].table_name
    assert [result.dimension for result in results] == [8, 16]
