"""Phase 11 Reranker：候选重排的抽象与 OpenAI-compatible 实现。

Reranker 是第二阶段精排：它只接收 Hybrid 已召回的候选，不能新增、
丢弃或改写证据来源。返回结果必须与输入候选一一对应，避免实验把
“候选截断”误读成“排序改善”。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from openai import OpenAI
from pydantic import BaseModel

from app.core.config import Settings, settings
from app.core.exceptions import DomainError


class RerankError(DomainError):
    """Reranker 配置缺失、上游失败或返回不合法。"""

    code = "rerank_error"
    http_status = 503


class _RerankResponse(BaseModel):
    """OpenAI-compatible /rerank 返回的最小契约。"""

    class Result(BaseModel):
        index: int
        relevance_score: float

    results: list[Result]


@dataclass(frozen=True)
class RerankCandidate:
    """一个待精排候选；``key`` 由调用方的 Hybrid 候选身份生成。"""

    key: str
    content: str


@dataclass(frozen=True)
class RerankedCandidate:
    """Reranker 返回的排序结果；分数必须有限且 key 不重复。"""

    key: str
    score: float


@runtime_checkable
class RerankClient(Protocol):
    """可替换的精排接口。"""

    @property
    def model(self) -> str: ...

    def rerank(
        self,
        query: str,
        candidates: Sequence[RerankCandidate],
        *,
        top_n: int,
    ) -> list[RerankedCandidate]: ...


class OpenAICompatibleRerankClient:
    """调用 OpenAI-compatible ``/v1/rerank`` 的精排客户端。"""

    def __init__(self, config: Settings | None = None) -> None:
        self._settings = config or settings
        if not self._settings.rerank_api_key and not _is_local_url(self._settings.rerank_base_url):
            raise RerankError(
                "RERANK_API_KEY 未配置",
                detail={"model": self._settings.rerank_model},
            )
        self._client = OpenAI(
            api_key=self._settings.rerank_api_key or "local-no-key",
            base_url=self._settings.rerank_base_url,
            timeout=self._settings.rerank_timeout,
            max_retries=self._settings.rerank_max_retries,
        )

    @property
    def model(self) -> str:
        return self._settings.rerank_model

    def rerank(
        self,
        query: str,
        candidates: Sequence[RerankCandidate],
        *,
        top_n: int,
    ) -> list[RerankedCandidate]:
        if not query.strip():
            raise RerankError("Rerank query 不能为空白")
        if not candidates:
            return []
        if top_n < 1:
            raise RerankError("top_n 必须 >= 1")
        if len({candidate.key for candidate in candidates}) != len(candidates):
            raise RerankError("Rerank 候选 key 重复")
        if any(not candidate.content.strip() for candidate in candidates):
            raise RerankError("Rerank 候选内容不能为空白")

        try:
            response = self._client.post(
                "/rerank",
                cast_to=_RerankResponse,
                body={
                    "model": self._settings.rerank_model,
                    "query": query,
                    "documents": [candidate.content for candidate in candidates],
                    "top_n": min(top_n, len(candidates)),
                },
            )
        except Exception as exc:  # noqa: BLE001 - 统一转换成领域错误
            raise RerankError(
                "Rerank 上游调用失败",
                detail={"model": self._settings.rerank_model},
            ) from exc

        by_index = {index: candidate for index, candidate in enumerate(candidates)}
        ranked: list[RerankedCandidate] = []
        seen: set[str] = set()
        for item in response.results:
            if item.index not in by_index:
                raise RerankError(
                    "Rerank 返回了不存在的候选位置",
                    detail={"index": item.index},
                )
            key = by_index[item.index].key
            if key in seen:
                raise RerankError("Rerank 返回了重复候选", detail={"key": key})
            if not math.isfinite(item.relevance_score):
                raise RerankError("Rerank 分数必须为有限数", detail={"key": key})
            seen.add(key)
            ranked.append(RerankedCandidate(key=key, score=item.relevance_score))
        if len(ranked) != min(top_n, len(candidates)):
            raise RerankError(
                "Rerank 返回数量不一致",
                detail={
                    "expected": min(top_n, len(candidates)),
                    "actual": len(ranked),
                },
            )
        return ranked


def _is_local_url(url: str) -> bool:
    from urllib.parse import urlparse

    return urlparse(url).hostname in {"127.0.0.1", "localhost", "::1"}


def get_rerank_client(config: Settings | None = None) -> RerankClient:
    """应用默认 Reranker 客户端。"""
    return OpenAICompatibleRerankClient(config)


__all__ = [
    "OpenAICompatibleRerankClient",
    "RerankCandidate",
    "RerankClient",
    "RerankError",
    "RerankedCandidate",
    "get_rerank_client",
]
