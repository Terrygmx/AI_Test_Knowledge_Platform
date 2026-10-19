"""Embedding 抽象测试（不访问网络）。"""

from __future__ import annotations

import pytest

from app.retrieval.embedding import (
    EmbeddingError,
    FakeEmbeddingClient,
    OpenAICompatibleEmbeddingClient,
    content_sha256,
)


class TestFakeEmbedding:
    def test_same_text_returns_same_vector(self) -> None:
        client = FakeEmbeddingClient(dimension=8)
        assert client.embed_query("支付失败") == client.embed_query("支付失败")

    def test_different_text_returns_different_vector(self) -> None:
        client = FakeEmbeddingClient(dimension=8)
        assert client.embed_query("支付失败") != client.embed_query("登录成功")

    def test_dimension_and_normalization(self) -> None:
        vector = FakeEmbeddingClient(dimension=8).embed_query("测试")
        assert len(vector) == 8
        norm = sum(value * value for value in vector) ** 0.5
        assert norm == pytest.approx(1.0)

    def test_batch_preserves_order(self) -> None:
        client = FakeEmbeddingClient(dimension=8)
        texts = ["a", "b", "c"]
        assert client.embed_texts(texts) == [client.embed_query(text) for text in texts]


class TestContentHash:
    def test_sha256_is_stable(self) -> None:
        assert content_sha256("支付失败") == content_sha256("支付失败")
        assert content_sha256("支付失败") != content_sha256("支付错误")


class TestOpenAICompatibleClient:
    def test_local_endpoint_allows_empty_api_key(self) -> None:
        from app.core.config import Settings

        config = Settings(
            embedding_base_url="http://127.0.0.1:28080/v1",
            embedding_api_key="",
            embedding_model="bge-m3-mlx-4bit",
        )
        client = OpenAICompatibleEmbeddingClient(config)
        assert client.dimension == 1024

    def test_remote_endpoint_requires_api_key(self) -> None:
        from app.core.config import Settings

        config = Settings(
            embedding_base_url="https://api.siliconflow.cn/v1",
            embedding_api_key="",
        )
        with pytest.raises(EmbeddingError, match="EMBEDDING_API_KEY"):
            OpenAICompatibleEmbeddingClient(config)

    def test_default_config_points_to_local_omlx(self) -> None:
        from app.core.config import Settings

        config = Settings()
        assert config.embedding_base_url == "http://127.0.0.1:28080/v1"
        assert config.embedding_model == "bge-m3-mlx-4bit"
        assert config.embedding_dim == 1024
