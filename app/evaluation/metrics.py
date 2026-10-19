"""检索评测指标：Recall@K / MRR。

这里的“相关 Chunk”不直接来自管理员标注的 Chunk ID，而是从 Gold Set 的原文证据
反向匹配：切分策略改变后，只要检索结果仍包含该证据，就仍算命中。
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Iterable, Sequence

from app.evaluation.dataset import EvaluationQuery
from app.models import KnowledgeChunk


def normalize_text(text: str) -> str:
    """为证据归属匹配做轻量规范化：统一 Unicode、空白和大小写。"""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(normalized.split())


def chunk_matches_evidence(chunk: KnowledgeChunk, evidence: str) -> bool:
    """Chunk 是否包含 Gold Set 的原文证据片段。"""
    needle = normalize_text(evidence)
    return bool(needle) and needle in normalize_text(chunk.content)


class RetrievalEvaluation:
    """按 Query 累积 Recall@K 与 MRR。"""

    def __init__(self, *, k_values: Sequence[int] = (5, 10)) -> None:
        self.k_values = tuple(sorted({value for value in k_values if value > 0}))
        if not self.k_values:
            raise ValueError("至少需要一个正整数 K")
        self._recall: dict[int, list[float]] = {k: [] for k in self.k_values}
        self._recall_hits: dict[int, int] = {k: 0 for k in self.k_values}
        self._recall_labels: dict[int, int] = {k: 0 for k in self.k_values}
        self._reciprocal_ranks: list[float] = []
        self.answerable_queries = 0
        self.unanswerable_queries = 0
        self.unanswerable_false_positives = 0
        self.unanswerable_top1_distances: list[float] = []

    def add(
        self,
        query: EvaluationQuery,
        results: Sequence[KnowledgeChunk],
        distances: Sequence[float] | None = None,
    ) -> None:
        """加入一个 Query 的检索结果。不可回答题只统计误召回。"""
        if not query.answerable:
            self.unanswerable_queries += 1
            if results:
                self.unanswerable_false_positives += 1
                if distances:
                    self.unanswerable_top1_distances.append(float(distances[0]))
            return

        self.answerable_queries += 1
        total_relevant = len(query.relevant)
        hit_at_k: dict[int, set[int]] = {}
        for k in self.k_values:
            top_k = results[:k]
            hit_at_k[k] = {
                index
                for index, label in enumerate(query.relevant)
                if any(
                    chunk.source_type == label.source_type
                    and chunk.source_id == label.source_id
                    and chunk_matches_evidence(chunk, label.evidence)
                    for chunk in top_k
                )
            }
            self._recall[k].append(len(hit_at_k[k]) / total_relevant)
            self._recall_hits[k] += len(hit_at_k[k])
            self._recall_labels[k] += total_relevant

        first_relevant_rank: int | None = None
        for rank, chunk in enumerate(results, start=1):
            if any(
                chunk.source_type == label.source_type
                and chunk.source_id == label.source_id
                and chunk_matches_evidence(chunk, label.evidence)
                for label in query.relevant
            ):
                first_relevant_rank = rank
                break
        self._reciprocal_ranks.append(
            1.0 / first_relevant_rank if first_relevant_rank is not None else 0.0
        )

    @property
    def recall(self) -> dict[int, float]:
        return {
            k: (sum(values) / len(values) if values else 0.0)
            for k, values in self._recall.items()
        }

    @property
    def mrr(self) -> float:
        if not self._reciprocal_ranks:
            return 0.0
        return sum(self._reciprocal_ranks) / len(self._reciprocal_ranks)

    def summary(self) -> dict[str, float | int]:
        result: dict[str, float | int] = {
            "answerable_queries": self.answerable_queries,
            "unanswerable_queries": self.unanswerable_queries,
            "unanswerable_false_positive_rate": (
                self.unanswerable_false_positives / self.unanswerable_queries
                if self.unanswerable_queries
                else 0.0
            ),
            "unanswerable_top1_distance_min": (
                min(self.unanswerable_top1_distances)
                if self.unanswerable_top1_distances
                else 0.0
            ),
            "unanswerable_top1_distance_median": (
                percentile(self.unanswerable_top1_distances, 0.5)
                if self.unanswerable_top1_distances
                else 0.0
            ),
            "mrr": self.mrr,
        }
        result.update({f"recall@{k}": value for k, value in self.recall.items()})
        result.update(
            {f"recall@{k}_hits": self._recall_hits[k] for k in self.k_values}
        )
        result.update(
            {f"recall@{k}_labels": self._recall_labels[k] for k in self.k_values}
        )
        return result


def percentile(values: Iterable[float], quantile: float) -> float:
    """简单 nearest-rank 分位数，用于记录延迟，不依赖 numpy 版本细节。"""
    ordered = sorted(values)
    if not ordered:
        return 0.0
    if not 0 < quantile <= 1:
        raise ValueError("quantile 必须在 (0, 1] 内")
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


__all__ = [
    "RetrievalEvaluation",
    "chunk_matches_evidence",
    "normalize_text",
    "percentile",
]
