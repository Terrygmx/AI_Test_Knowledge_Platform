"""检索评测指标：Recall@K / Precision@K / MRR / NDCG@K。

这里的“相关 Chunk”不直接来自管理员标注的 Chunk ID，而是从 Gold Set 的原文证据
反向匹配：切分策略改变后，只要检索结果仍包含该证据，就仍算命中。

Phase 13 把指标拆成可单独测试的纯函数。聚合器保留原有 Recall/MRR 口径，
并额外输出 Precision@K、NDCG@K 与逐 Query Badcase 归因。
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Hashable, Iterable, Sequence
from typing import Any

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


def _validate_k(k: int) -> None:
    if k < 1:
        raise ValueError("k 必须是正整数")


def recall_at_k(retrieved: Sequence[Hashable], gold: Sequence[Hashable], k: int) -> float:
    """返回 Top-K 命中的 Gold 数 / Gold 总数。

    Gold 是集合语义：重复标注只计算一次。Gold 为空表示没有可计算的相关项，
    返回 0.0 而不是制造 ``0/0`` 的隐式结果。
    """
    _validate_k(k)
    gold_set = set(gold)
    if not gold_set:
        return 0.0
    return len(gold_set.intersection(retrieved[:k])) / len(gold_set)


def precision_at_k(retrieved: Sequence[Hashable], gold: Sequence[Hashable], k: int) -> float:
    """返回 Top-K 中相关项数 / K，截断到实际返回数量。"""
    _validate_k(k)
    gold_set = set(gold)
    if not gold_set:
        return 0.0
    top_k = retrieved[:k]
    if not top_k:
        return 0.0
    return len(gold_set.intersection(top_k)) / len(top_k)


def reciprocal_rank(retrieved: Sequence[Hashable], gold: Sequence[Hashable]) -> float:
    """返回第一条相关结果的倒数排名；没有命中时为 0。"""
    gold_set = set(gold)
    if not gold_set:
        return 0.0
    for rank, item in enumerate(retrieved, start=1):
        if item in gold_set:
            return 1.0 / rank
    return 0.0


def mrr(
    retrieved: Sequence[Sequence[Hashable]],
    gold: Sequence[Sequence[Hashable]],
) -> float:
    """对多条 Query 的 reciprocal rank 取平均。"""
    if len(retrieved) != len(gold):
        raise ValueError("retrieved 与 gold 的 Query 数必须一致")
    if not gold:
        return 0.0
    return sum(
        reciprocal_rank(items, labels) for items, labels in zip(retrieved, gold, strict=True)
    ) / len(gold)


def ndcg_at_k(
    retrieved: Sequence[Hashable],
    gold: Sequence[Hashable],
    k: int,
) -> float:
    """返回二值相关性的 NDCG@K。

    每条 Gold 证据默认 gain=1。DCG 使用 ``gain / log2(rank + 1)``；
    IDCG 是同样数量相关项排在最前时的 DCG。重复 Gold 去重，避免同一证据
    被重复计入理想排序。
    """
    _validate_k(k)
    gold_set = set(gold)
    if not gold_set:
        return 0.0

    dcg = 0.0
    matched: set[Hashable] = set()
    for rank, item in enumerate(retrieved[:k], start=1):
        if item in gold_set and item not in matched:
            dcg += 1.0 / math.log2(rank + 1)
            matched.add(item)
    ideal_hits = min(len(gold_set), k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / idcg if idcg else 0.0


def _evidence_matches(
    chunk: Any,
    label: Any,
) -> bool:
    """统一处理 ORM / 实验适配器 Chunk 与 Gold 标签。"""
    return (
        chunk.source_type == label.source_type
        and chunk.source_id == label.source_id
        and chunk_matches_evidence(chunk, label.evidence)
    )


def query_metric_details(
    query: EvaluationQuery,
    results: Sequence[Any],
    *,
    k_values: Sequence[int] = (5, 10),
) -> dict[str, Any]:
    """计算单条 Query 的指标、命中证据与 Badcase 初筛原因。

    Badcase 只做检索层归因，不把“未命中”直接归咎于生成或重排：

    - ``empty_results``：Top-K 为空；
    - ``wrong_source``：出现了 Gold 来源但证据片段不匹配；
    - ``no_gold_hit``：未找到可匹配的 Gold 证据；
    - ``ranked_below_k``：证据存在但排在请求的 K 之外。

    不可回答 Query 返回 ``unexpected_results``，用于区分“本应拒答但返回了内容”。
    """
    if not k_values:
        raise ValueError("至少需要一个正整数 K")
    validated_k = tuple(sorted({value for value in k_values if value > 0}))
    if not validated_k:
        raise ValueError("至少需要一个正整数 K")

    if not query.answerable:
        return {
            "query_id": query.query_id,
            "answerable": False,
            "result_count": len(results),
            "badcase": "unexpected_results" if results else None,
            "hit_labels": [],
        }

    gold_set = list(query.relevant)
    matched_labels: list[int] = []
    for label_index, label in enumerate(gold_set):
        if any(_evidence_matches(chunk, label) for chunk in results):
            matched_labels.append(label_index)

    first_rank: int | None = None
    for rank, chunk in enumerate(results, start=1):
        if any(_evidence_matches(chunk, label) for label in gold_set):
            first_rank = rank
            break

    metrics: dict[str, float | int] = {
        "recall": len(matched_labels) / len(gold_set) if gold_set else 0.0,
        "reciprocal_rank": 1.0 / first_rank if first_rank else 0.0,
    }
    for k in validated_k:
        top_k = results[:k]
        hit_count = sum(
            1 for label in gold_set if any(_evidence_matches(chunk, label) for chunk in top_k)
        )
        metrics[f"recall@{k}"] = hit_count / len(gold_set) if gold_set else 0.0
        metrics[f"precision@{k}"] = hit_count / len(top_k) if top_k else 0.0
        metrics[f"ndcg@{k}"] = ndcg_at_k(
            [
                index
                for index, chunk in enumerate(top_k, start=1)
                if any(_evidence_matches(chunk, label) for label in gold_set)
            ],
            list(range(1, len(gold_set) + 1)),
            k,
        )

    badcase: str | None = None
    if not results:
        badcase = "empty_results"
    elif not matched_labels:
        has_source_match = any(
            any(
                chunk.source_type == label.source_type and chunk.source_id == label.source_id
                for label in gold_set
            )
            for chunk in results
        )
        badcase = "wrong_source" if has_source_match else "no_gold_hit"
    elif first_rank is not None and first_rank > validated_k[-1]:
        badcase = "ranked_below_k"

    return {
        "query_id": query.query_id,
        "answerable": True,
        "result_count": len(results),
        "first_relevant_rank": first_rank,
        "hit_labels": matched_labels,
        "metrics": metrics,
        "badcase": badcase,
    }


class RetrievalEvaluation:
    """按 Query 累积 Recall@K、Precision@K、MRR 与 NDCG@K。"""

    def __init__(self, *, k_values: Sequence[int] = (5, 10)) -> None:
        self.k_values = tuple(sorted({value for value in k_values if value > 0}))
        if not self.k_values:
            raise ValueError("至少需要一个正整数 K")
        self._recall: dict[int, list[float]] = {k: [] for k in self.k_values}
        self._precision: dict[int, list[float]] = {k: [] for k in self.k_values}
        self._ndcg: dict[int, list[float]] = {k: [] for k in self.k_values}
        self._recall_hits: dict[int, int] = {k: 0 for k in self.k_values}
        self._recall_labels: dict[int, int] = {k: 0 for k in self.k_values}
        self._reciprocal_ranks: list[float] = []
        self._query_details: list[dict[str, Any]] = []
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
            self._query_details.append(query_metric_details(query, results, k_values=self.k_values))
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
            self._precision[k].append(len(hit_at_k[k]) / len(top_k) if top_k else 0.0)
            self._ndcg[k].append(
                ndcg_at_k(
                    [
                        rank
                        for rank, chunk in enumerate(top_k, start=1)
                        if any(_evidence_matches(chunk, label) for label in query.relevant)
                    ],
                    list(range(1, total_relevant + 1)),
                    k,
                )
            )

        first_relevant_rank: int | None = None
        for rank, chunk in enumerate(results, start=1):
            if any(_evidence_matches(chunk, label) for label in query.relevant):
                first_relevant_rank = rank
                break
        self._reciprocal_ranks.append(
            1.0 / first_relevant_rank if first_relevant_rank is not None else 0.0
        )
        self._query_details.append(query_metric_details(query, results, k_values=self.k_values))

    @property
    def recall(self) -> dict[int, float]:
        return {
            k: (sum(values) / len(values) if values else 0.0) for k, values in self._recall.items()
        }

    @property
    def precision(self) -> dict[int, float]:
        return {
            k: (sum(values) / len(values) if values else 0.0)
            for k, values in self._precision.items()
        }

    @property
    def ndcg(self) -> dict[int, float]:
        return {
            k: (sum(values) / len(values) if values else 0.0) for k, values in self._ndcg.items()
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
                min(self.unanswerable_top1_distances) if self.unanswerable_top1_distances else 0.0
            ),
            "unanswerable_top1_distance_median": (
                percentile(self.unanswerable_top1_distances, 0.5)
                if self.unanswerable_top1_distances
                else 0.0
            ),
            "mrr": self.mrr,
        }
        result.update({f"recall@{k}": value for k, value in self.recall.items()})
        result.update({f"precision@{k}": value for k, value in self.precision.items()})
        result.update({f"ndcg@{k}": value for k, value in self.ndcg.items()})
        result.update({f"recall@{k}_hits": self._recall_hits[k] for k in self.k_values})
        result.update({f"recall@{k}_labels": self._recall_labels[k] for k in self.k_values})
        badcases = [
            {
                "query_id": item["query_id"],
                "badcase": item["badcase"],
                "answerable": item["answerable"],
                "result_count": item["result_count"],
                "first_relevant_rank": item.get("first_relevant_rank"),
            }
            for item in self._query_details
            if item["badcase"]
        ]
        result["badcase_count"] = len(badcases)
        result["badcases"] = badcases
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
    "mrr",
    "ndcg_at_k",
    "normalize_text",
    "percentile",
    "precision_at_k",
    "query_metric_details",
    "recall_at_k",
    "reciprocal_rank",
]
