"""Phase 11 Hybrid + Rerank：只重排融合候选，不改变候选集合。"""

from __future__ import annotations

import math
from collections.abc import Sequence

from app.rerank.service import RerankCandidate, RerankClient, RerankError
from app.retrieval.hybrid import FusedHit


def rerank_fused_hits(
    hits: Sequence[FusedHit],
    query: str,
    reranker: RerankClient,
    *,
    top_n: int,
) -> list[FusedHit]:
    """按精排分数还原 FusedHit 顺序。

    Reranker 只返回身份与分数，不返回正文；这里强制从原始 Fusion 候选取回
    内容，防止上游或适配器伪造候选。
    """
    if top_n < 1:
        raise ValueError("top_n 必须 >= 1")
    if not hits:
        return []
    by_key = {hit.key: hit for hit in hits}
    if len(by_key) != len(hits):
        raise ValueError("Hybrid 候选身份重复")
    candidates = [RerankCandidate(key=hit.key_str(), content=hit.content) for hit in hits]
    ranked = reranker.rerank(query, candidates, top_n=top_n)
    if len(ranked) != min(top_n, len(hits)):
        raise RerankError("Rerank 返回数量不一致")
    key_to_tuple = {hit.key_str(): hit.key for hit in hits}
    if any(item.key not in key_to_tuple for item in ranked):
        raise RerankError("Rerank 返回了未知候选")
    if any(not math.isfinite(item.score) for item in ranked):
        raise RerankError("Rerank 分数必须是有限数")
    return [by_key[key_to_tuple[item.key]] for item in ranked]


__all__ = ["rerank_fused_hits"]
