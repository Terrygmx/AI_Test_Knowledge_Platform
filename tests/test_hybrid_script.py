"""Phase 9 实验脚本与 manifest 的离线测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.evaluation.dataset import load_dataset
from app.evaluation.hybrid_experiment import (
    HybridCandidate,
    HybridChannelResult,
    HybridExperimentResult,
)
from scripts import hybrid_experiment
from scripts.hybrid_experiment import _fetch_model_metadata, _manifest, build_engine

DATASET = "eval/fixtures/phase8_simulated.json"


def _result() -> HybridExperimentResult:
    metrics = {
        "answerable_queries": 18,
        "unanswerable_queries": 6,
        "unanswerable_false_positive_rate": 0.0,
        "unanswerable_top1_distance_min": 0.0,
        "unanswerable_top1_distance_median": 0.0,
        "mrr": 0.5,
        "recall@5": 0.5,
        "recall@10": 0.5,
        "recall@5_hits": 13,
        "recall@10_hits": 13,
        "recall@5_labels": 26,
        "recall@10_labels": 26,
    }
    channels = [
        HybridChannelResult(
            name=name,
            strategy=name,
            metrics=metrics,
            query_p50_seconds=0.01,
            query_p95_seconds=0.02,
            vector_candidate_hits=20,
            keyword_candidate_hits=20,
            fused_candidates=10,
        )
        for name in ("vector", "keyword", "weighted", "rrf")
    ]
    return HybridExperimentResult(
        channels=channels,
        dataset_queries=24,
        chunk_count=47,
        index_seconds=1.2,
        query_embedding_seconds=0.8,
    )


def _manifest_for(**overrides):
    dataset = load_dataset(DATASET)
    now = datetime.now(UTC)
    kwargs = {
        "dataset_path": DATASET,
        "started_at": now,
        "finished_at": now,
        "command": ["python", "scripts/hybrid_experiment.py"],
        "candidate_k": 20,
        "top_k": 10,
        "fusion": "rrf",
        "vector_weight": 1.0,
        "keyword_weight": 1.0,
        "rrf_k": 60,
        "embedding_model": "bge-m3-mlx-4bit",
        "embedding_dimension": 1024,
        "embedding_batch_size": 32,
        "embedding_timeout_seconds": 60.0,
        "embedding_max_retries": 2,
        "model_metadata": {"status": "available", "models": {"bge-m3-mlx-4bit": {}}},
        "candidate_sha256": "a" * 64,
        "database_url": "postgresql+psycopg://user:secret@localhost/aitkp_hybrid",
    }
    kwargs.update(overrides)
    return _manifest(dataset, _result(), **kwargs)


def test_manifest_records_phase9_parameters_and_channel_results() -> None:
    manifest = _manifest_for()

    assert manifest["phase"] == 9
    assert manifest["retrieval"] == {
        "candidate_k": 20,
        "top_k": 10,
        "fusion": "rrf",
        "vector_weight": 1.0,
        "keyword_weight": 1.0,
        "rrf_k": 60,
        "normalization": "per-channel-min-max",
    }
    assert manifest["chunks"]["count"] == 47
    assert [item["name"] for item in manifest["results"]] == [
        "vector",
        "keyword",
        "weighted",
        "rrf",
    ]
    assert isinstance(manifest["git_dirty"], bool)
    assert manifest["environment"]["database_name"] == "aitkp_hybrid"
    assert manifest["environment"]["embedding_batch_size"] == 32
    assert manifest["environment"]["embedding_timeout_seconds"] == 60.0
    assert manifest["environment"]["embedding_max_retries"] == 2
    assert manifest["embedding_service"]["model_metadata"]["status"] == "available"


def test_manifest_redacts_database_credentials() -> None:
    manifest = _manifest_for(
        command=[
            "python",
            "scripts/hybrid_experiment.py",
            "--database-url",
            "postgresql+psycopg://user:secret@localhost/aitkp_hybrid",
        ]
    )

    assert manifest["command"][-1] == "postgresql+psycopg://user:***@localhost/aitkp_hybrid"


def test_fetch_model_metadata_records_provider_fields(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "data": [
                    {
                        "id": "bge-m3-mlx-4bit",
                        "object": "model",
                        "owned_by": "omlx",
                        "max_model_len": 8194,
                    }
                ]
            }

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr(hybrid_experiment.httpx, "get", fake_get)

    metadata = _fetch_model_metadata(
        base_url="http://127.0.0.1:28080/v1/",
        api_key="omlx",
        model_names=["bge-m3-mlx-4bit"],
        timeout=12.5,
    )

    assert captured["url"] == "http://127.0.0.1:28080/v1/models"
    assert captured["headers"] == {"Authorization": "Bearer omlx"}
    assert captured["timeout"] == 12.5
    assert metadata == {
        "status": "available",
        "models": {
            "bge-m3-mlx-4bit": {
                "object": "model",
                "owned_by": "omlx",
                "max_model_len": 8194,
            }
        },
        "missing_models": [],
    }


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"candidate_k": 5, "top_k": 10}, "candidate_k"),
        ({"fusion": "bad"}, "fusion"),
        ({"rrf_k": 0}, "rrf_k"),
        ({"vector_weight": -1}, "权重"),
    ],
)
def test_manifest_rejects_invalid_retrieval_configuration(kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        _manifest_for(**kwargs)


def test_build_engine_requires_hybrid_database() -> None:
    with pytest.raises(ValueError, match="hybrid"):
        build_engine("postgresql+psycopg://user:pass@localhost/ai_test_knowledge")
    with pytest.raises(ValueError, match="SQLite"):
        build_engine("sqlite://")


def test_hybrid_candidate_key_includes_chunk_index() -> None:
    left = HybridCandidate("document", "DOC-1", 0, "a", (1.0,))
    right = HybridCandidate("document", "DOC-1", 1, "b", (1.0,))

    assert left.key != right.key
