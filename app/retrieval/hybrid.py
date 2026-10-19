"""Phase 9 混合检索：通道候选与融合的纯逻辑。

本模块只处理已经召回的候选，不直接访问数据库或模型服务：

- ``weighted``：把余弦相似度与归一化 BM25 分数加权相加；
- ``rrf``：只使用通道名次，不要求跨通道分数可比。

候选以 ``(source_type, source_id, chunk_index)`` 去重。保留 ``chunk_index``
很重要：同一个来源有多个 Chunk 时，不能把不同证据片段合并成一条。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class ChannelHit:
    """一个召回通道返回的候选。

    ``score`` 的方向由通道决定：向量通道使用 cosine distance（越小越好），
    关键词通道使用 BM25（越大越好）。融合器负责按 ``higher_is_better`` 归一化。
    """

    source_type: str
    source_id: str
    chunk_index: int
    score: float
    content: str = ""
    higher_is_better: bool = True
    matched_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class FusedHit:
    """融合后的候选及其可追溯来源。"""

    source_type: str
    source_id: str
    chunk_index: int
    score: float
    content: str
    vector_rank: int | None
    keyword_rank: int | None
    vector_score: float | None
    keyword_score: float | None
    matched_terms: tuple[str, ...]

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.source_type, self.source_id, self.chunk_index)


def _key(hit: ChannelHit) -> tuple[str, str, int]:
    return (hit.source_type, hit.source_id, hit.chunk_index)


def _deduplicate(hits: Sequence[ChannelHit]) -> list[ChannelHit]:
    """保留通道内首次出现（即排名最高）的候选。"""
    seen: set[tuple[str, str, int]] = set()
    result: list[ChannelHit] = []
    for hit in hits:
        key = _key(hit)
        if key in seen:
            continue
        seen.add(key)
        result.append(hit)
    return result


def _min_max(values: Sequence[float], *, higher_is_better: bool) -> list[float]:
    """把一个通道的分数映射到 [0, 1]；全相等时统一给 1。"""
    if not values:
        return []
    normalized_values = list(values) if higher_is_better else [-value for value in values]
    low = min(normalized_values)
    high = max(normalized_values)
    if high == low:
        return [1.0] * len(values)
    return [(value - low) / (high - low) for value in normalized_values]


def _channel_direction(
    hits: Sequence[ChannelHit],
    *,
    channel: str,
) -> bool:
    """校验同一通道的分数方向一致，并返回该方向。"""
    directions = {hit.higher_is_better for hit in hits}
    if len(directions) > 1:
        raise ValueError(f"{channel} 通道内的 higher_is_better 方向不一致")
    return next(iter(directions), True)


def _validate_finite_hits(hits: Sequence[ChannelHit], *, channel: str) -> None:
    if any(not math.isfinite(hit.score) for hit in hits):
        raise ValueError(f"{channel} 通道分数必须是有限数")


def fuse(
    vector_hits: Sequence[ChannelHit],
    keyword_hits: Sequence[ChannelHit],
    *,
    strategy: str = "rrf",
    vector_weight: float = 1.0,
    keyword_weight: float = 1.0,
    rrf_k: int = 60,
    limit: int | None = None,
) -> list[FusedHit]:
    """融合两个通道，返回按融合分数降序的候选。

    Weighted 使用每个通道内部的 min-max 归一化，避免把距离与 BM25 原始分数
    直接相加；RRF 使用行业常见的 ``1 / (rrf_k + rank)``（rank 从 1 开始），
    因此不依赖分数的量纲。调用方按 score 降序、身份稳定排序。
    """
    if strategy not in {"weighted", "rrf"}:
        raise ValueError("strategy 必须是 weighted 或 rrf")
    if not math.isfinite(vector_weight) or not math.isfinite(keyword_weight):
        raise ValueError("通道权重必须是有限数")
    if vector_weight < 0 or keyword_weight < 0:
        raise ValueError("通道权重必须 >= 0")
    if vector_weight == 0 and keyword_weight == 0:
        raise ValueError("至少一个通道权重大于 0")
    if rrf_k < 1:
        raise ValueError("rrf_k 必须 >= 1")
    if limit is not None and limit < 1:
        raise ValueError("limit 必须大于 0")

    vector_unique = _deduplicate(vector_hits)
    keyword_unique = _deduplicate(keyword_hits)
    _validate_finite_hits(vector_unique, channel="vector")
    _validate_finite_hits(keyword_unique, channel="keyword")
    vector_direction = _channel_direction(vector_unique, channel="vector")
    keyword_direction = _channel_direction(keyword_unique, channel="keyword")
    vector_by_key = {_key(hit): hit for hit in vector_unique}
    keyword_by_key = {_key(hit): hit for hit in keyword_unique}
    keys = set(vector_by_key) | set(keyword_by_key)

    # 两个通道的候选在进入融合前已经按各自方向排好；不要在这里用分数重排。
    # 向量通道的 distance 越小越好，若误用统一降序，会把最差向量排成第 1 名。
    # 调用方若传入未排序候选，应由适配层负责排序，而不是让融合器猜测方向。

    if strategy == "rrf":
        vector_scores = {
            _key(hit): vector_weight / (rrf_k + rank)
            for rank, hit in enumerate(vector_unique, start=1)
        }
        keyword_scores = {
            _key(hit): keyword_weight / (rrf_k + rank)
            for rank, hit in enumerate(keyword_unique, start=1)
        }
    else:
        vector_values = _min_max(
            [hit.score for hit in vector_unique],
            higher_is_better=vector_direction,
        )
        keyword_values = _min_max(
            [hit.score for hit in keyword_unique],
            higher_is_better=keyword_direction,
        )
        vector_scores = {
            _key(hit): vector_weight * value
            for hit, value in zip(vector_unique, vector_values, strict=True)
        }
        keyword_scores = {
            _key(hit): keyword_weight * value
            for hit, value in zip(keyword_unique, keyword_values, strict=True)
        }

    vector_rank = {_key(hit): rank for rank, hit in enumerate(vector_unique, start=1)}
    keyword_rank = {_key(hit): rank for rank, hit in enumerate(keyword_unique, start=1)}
    fused: list[FusedHit] = []
    for key in keys:
        vector_hit = vector_by_key.get(key)
        keyword_hit = keyword_by_key.get(key)
        representative = vector_hit or keyword_hit
        assert representative is not None
        fused.append(
            FusedHit(
                source_type=key[0],
                source_id=key[1],
                chunk_index=key[2],
                score=vector_scores.get(key, 0.0) + keyword_scores.get(key, 0.0),
                content=representative.content,
                vector_rank=vector_rank.get(key),
                keyword_rank=keyword_rank.get(key),
                vector_score=vector_hit.score if vector_hit is not None else None,
                keyword_score=keyword_hit.score if keyword_hit is not None else None,
                matched_terms=keyword_hit.matched_terms if keyword_hit is not None else (),
            )
        )

    return sorted(
        fused,
        key=lambda item: (-item.score, item.source_type, item.source_id, item.chunk_index),
    )[:limit]


def channel_hits(
    items: Iterable[tuple[str, str, int, float]],
    *,
    higher_is_better: bool,
) -> list[ChannelHit]:
    """便捷转换，便于实验脚本从数据库结果构造通道候选。"""
    return [
        ChannelHit(
            source_type=source_type,
            source_id=source_id,
            chunk_index=chunk_index,
            score=score,
            higher_is_better=higher_is_better,
        )
        for source_type, source_id, chunk_index, score in items
    ]


__all__ = ["ChannelHit", "FusedHit", "channel_hits", "fuse"]
