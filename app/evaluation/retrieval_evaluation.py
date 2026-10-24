"""Phase 13 检索评测运行器。

本模块只负责“给定检索器与 Gold Set，计算指标并保留可解释细节”，不调用
Embedding、数据库或 LLM。Phase 13 的正式输入应来自已人工确认的 Phase 12
数据集；候选数据只能用于流程演练，并必须显式标记 ``candidate``。
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.evaluation.dataset import ChunkingDataset
from app.evaluation.lexical_experiment import LexicalSearcher, k_values_for
from app.evaluation.metrics import RetrievalEvaluation, percentile, query_metric_details


@dataclass
class RetrievalEvaluationResult:
    """一次检索评测的总体、分类、逐 Query 与延迟结果。"""

    name: str
    mode: str
    top_k: int
    k_values: tuple[int, ...]
    metrics: dict[str, float | int | list[dict[str, Any]]]
    categories: dict[str, dict[str, float | int | list[dict[str, Any]]]]
    query_details: list[dict[str, Any]]
    query_latencies: list[float] = field(default_factory=list)
    query_p50_seconds: float = 0.0
    query_p95_seconds: float = 0.0
    index_seconds: float = 0.0


def evaluate_retriever(
    dataset: ChunkingDataset,
    searcher: LexicalSearcher,
    *,
    name: str,
    mode: str,
    top_k: int = 10,
) -> RetrievalEvaluationResult:
    """评测一个检索器，并输出总体、分类与逐 Query 指标。

    ``mode`` 只允许 ``candidate`` / ``formal``。正式模式要求数据集本身已通过
    外部人工确认校验；本函数不负责伪造或改写审阅状态。
    """
    if top_k < 1:
        raise ValueError("top_k 必须大于 0")
    if mode not in {"candidate", "formal"}:
        raise ValueError("mode 必须是 candidate 或 formal")

    k_values = k_values_for(top_k)
    overall = _evaluate_queries(dataset.queries, searcher, top_k=top_k, k_values=k_values)
    categories: dict[str, dict[str, float | int | list[dict[str, Any]]]] = {}
    for category in sorted({query.category for query in dataset.queries}):
        category_queries = [query for query in dataset.queries if query.category == category]
        categories[category] = _evaluate_queries(
            category_queries,
            searcher,
            top_k=top_k,
            k_values=k_values,
        )[0]

    return RetrievalEvaluationResult(
        name=name,
        mode=mode,
        top_k=top_k,
        k_values=k_values,
        metrics=overall[0],
        categories=categories,
        query_details=overall[1],
        query_latencies=overall[2],
        query_p50_seconds=percentile(overall[2], 0.5),
        query_p95_seconds=percentile(overall[2], 0.95),
        index_seconds=searcher.index_seconds,
    )


def _evaluate_queries(
    queries: Sequence[Any],
    searcher: LexicalSearcher,
    *,
    top_k: int,
    k_values: Sequence[int],
) -> tuple[
    dict[str, float | int | list[dict[str, Any]]],
    list[dict[str, Any]],
    list[float],
]:
    evaluator = RetrievalEvaluation(k_values=k_values)
    details: list[dict[str, Any]] = []
    latencies: list[float] = []
    for query in queries:
        started = time.perf_counter()
        results = searcher.search(query.query, limit=top_k)
        latencies.append(time.perf_counter() - started)
        evaluator.add(query, results)
        details.append(query_metric_details(query, results, k_values=k_values))
    summary = evaluator.summary()
    summary["badcases"] = [item for item in details if item["badcase"]]
    return summary, details, latencies


def retrieval_result_sha256(result: RetrievalEvaluationResult) -> str:
    """对可复现结果做稳定哈希，不包含延迟等环境噪声。"""
    payload = {
        "name": result.name,
        "mode": result.mode,
        "top_k": result.top_k,
        "k_values": list(result.k_values),
        "metrics": result.metrics,
        "categories": result.categories,
        "query_details": result.query_details,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "RetrievalEvaluationResult",
    "evaluate_retriever",
    "retrieval_result_sha256",
]
