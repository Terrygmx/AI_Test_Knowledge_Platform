"""Embedding 抽象与 OpenAI-compatible 实现。

原则：

- 业务代码依赖 ``EmbeddingClient`` 协议，不直接依赖具体厂商 SDK；
- 测试使用确定性 Fake，不需要网络；
- 批量调用保留输入顺序，任何数量 / 维度不匹配都必须 fail fast；
- 向量只做检索表示，原始文本与来源信息仍是事实来源。
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Protocol, runtime_checkable
from urllib.parse import urlparse

from openai import OpenAI

from app.core.config import Settings, settings
from app.core.exceptions import DomainError
from app.core.logging import get_logger

logger = get_logger(__name__)


@runtime_checkable
class EmbeddingClient(Protocol):
    """可替换的 Embedding 接口。"""

    @property
    def dimension(self) -> int: ...

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class EmbeddingError(DomainError):
    """Embedding 调用失败（配置缺失、上游错误、返回不合法）。"""

    code = "embedding_error"
    http_status = 503


def content_sha256(text: str) -> str:
    """内容哈希：用于增量索引判断内容是否发生变化。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _validate_vectors(
    vectors: list[list[float]],
    *,
    expected_count: int,
    dimension: int,
) -> list[list[float]]:
    if len(vectors) != expected_count:
        raise EmbeddingError(
            "Embedding 返回数量不一致",
            detail={"expected": expected_count, "actual": len(vectors)},
        )
    for index, vector in enumerate(vectors):
        if len(vector) != dimension:
            raise EmbeddingError(
                "Embedding 维度不一致",
                detail={"index": index, "expected": dimension, "actual": len(vector)},
            )
        if not all(math.isfinite(value) for value in vector):
            raise EmbeddingError("Embedding 包含非有限数值", detail={"index": index})
    return vectors


class OpenAICompatibleEmbeddingClient:
    """OpenAI-compatible Embedding 客户端（默认本机 OMLX bge-m3）。"""

    def __init__(
        self,
        config: Settings | None = None,
        *,
        allow_non_production_dimension: bool = False,
    ) -> None:
        """``allow_non_production_dimension`` 仅供使用独立表的离线实验。"""
        self._settings = config or settings
        self._dimension = self._settings.embedding_dim
        if not self._settings.embedding_api_key and not _is_local_url(
            self._settings.embedding_base_url
        ):
            raise EmbeddingError(
                "EMBEDDING_API_KEY 未配置",
                detail={"model": self._settings.embedding_model},
            )
        self._client = OpenAI(
            # OpenAI SDK 要求非空 Key；本机服务可用占位值。
            api_key=self._settings.embedding_api_key or "local-no-key",
            base_url=self._settings.embedding_base_url,
            timeout=60.0,
            max_retries=2,
        )

        # 数据库列是 Vector(EMBEDDING_DIM)；配置漂移必须在进程启动 / 建客户端时暴露。
        if self._dimension != 1024 and not allow_non_production_dimension:
            logger.warning(
                "EMBEDDING_DIM=%s 与数据库向量维度 1024 不一致，写库将被拒绝",
                self._dimension,
            )

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        if any(not text.strip() for text in texts):
            raise EmbeddingError("Embedding 输入不能包含空白文本")

        vectors: list[list[float]] = []
        batch_size = max(self._settings.embedding_batch_size, 1)
        for start in range(0, len(texts), batch_size):
            batch = list(texts[start : start + batch_size])
            try:
                response = self._client.embeddings.create(
                    model=self._settings.embedding_model,
                    input=batch,
                )
            except Exception as exc:  # noqa: BLE001 - 统一转换成领域错误
                raise EmbeddingError(
                    "Embedding 上游调用失败",
                    detail={"model": self._settings.embedding_model},
                ) from exc

            ordered = sorted(response.data, key=lambda item: item.index)
            vectors.extend([list(item.embedding) for item in ordered])

        return _validate_vectors(
            vectors,
            expected_count=len(texts),
            dimension=self._dimension,
        )

    def embed_query(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]


class FakeEmbeddingClient:
    """确定性 Fake Embedding：仅用于测试 / 离线演练。

    这不是语义模型，不能用于生产检索；它只保证相同文本得到相同向量，
    且不同文本得到不同向量，从而验证 Pipeline 的连接和维度检查。
    """

    def __init__(self, dimension: int = 8) -> None:
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text)

    def _embed_one(self, text: str) -> list[float]:
        # 用计数器扩展摘要，支持任意维度（测试常用 8 维，数据库列是 1024 维）。
        values: list[int] = []
        counter = 0
        while len(values) < self._dimension:
            values.extend(hashlib.sha256(f"{counter}:{text}".encode()).digest())
            counter += 1
        raw = [value / 255.0 - 0.5 for value in values[: self._dimension]]
        norm = math.sqrt(sum(value * value for value in raw))
        if norm == 0:
            return [0.0] * self._dimension
        return [value / norm for value in raw]


def get_embedding_client(config: Settings | None = None) -> EmbeddingClient:
    """应用默认 Embedding 客户端；远端服务未配 Key 时抛出可读错误。"""
    return OpenAICompatibleEmbeddingClient(config)


def _is_local_url(url: str) -> bool:
    host = urlparse(url).hostname
    return host in {"127.0.0.1", "localhost", "::1"}


__all__ = [
    "EmbeddingClient",
    "EmbeddingError",
    "FakeEmbeddingClient",
    "OpenAICompatibleEmbeddingClient",
    "content_sha256",
    "get_embedding_client",
]
