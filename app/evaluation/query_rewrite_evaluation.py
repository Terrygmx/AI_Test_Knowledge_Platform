"""Phase 16 Query Rewrite 对比评测。

只比较改写文本对检索指标和延迟的影响；不把 candidate 结果冒充正式 Gold Set。
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.evaluation.dataset import ChunkingDataset
from app.evaluation.lexical_experiment import LexicalSearcher, k_values_for
from app.evaluation.metrics import RetrievalEvaluation, query_metric_details
from app.evaluation.phase12_dataset import Phase12Dataset


class QueryRewriterLike(Protocol):
    name: str

    def rewrite(self, query: str) -> Any: ...


class QueryUnderstandingLike(Protocol):
    name: str

    def understand(self, query: str) -> Any: ...


@dataclass
class RewriteEvaluationResult:
    name: str
    mode: str
    metrics: dict[str, float | int | list[dict[str, Any]]]
    query_details: list[dict[str, Any]]
    rewrite_seconds: list[float]
    search_seconds: list[float]


def _evaluate_variant(
    dataset: ChunkingDataset,
    searcher: LexicalSearcher,
    rewriter: QueryRewriterLike,
    *,
    mode: str,
    top_k: int,
) -> RewriteEvaluationResult:
    if top_k < 1:
        raise ValueError("top_k 必须大于 0")
    if mode not in {"candidate", "formal"}:
        raise ValueError("mode 必须是 candidate 或 formal")
    evaluator = RetrievalEvaluation(k_values=k_values_for(top_k))
    details: list[dict[str, Any]] = []
    rewrite_seconds: list[float] = []
    search_seconds: list[float] = []
    for query in dataset.queries:
        rewrite_started = time.perf_counter()
        rewrite_result = rewriter.rewrite(query.query)
        rewrite_seconds.append(time.perf_counter() - rewrite_started)
        search_started = time.perf_counter()
        results = searcher.search(rewrite_result.search_query, limit=top_k)
        search_seconds.append(time.perf_counter() - search_started)
        evaluator.add(query, results)
        detail = query_metric_details(query, results, k_values=k_values_for(top_k))
        detail["original_query"] = query.query
        detail["search_query"] = rewrite_result.search_query
        detail["changed"] = rewrite_result.changed
        details.append(detail)
    summary = evaluator.summary()
    summary["rewrite_seconds_total"] = sum(rewrite_seconds)
    summary["rewrite_seconds_p95"] = (
        sorted(rewrite_seconds)[max(0, int(len(rewrite_seconds) * 0.95) - 1)]
        if rewrite_seconds
        else 0.0
    )
    summary["search_seconds_total"] = sum(search_seconds)
    summary["changed_queries"] = sum(bool(item["changed"]) for item in details)
    summary["badcases"] = [item for item in details if item["badcase"]]
    return RewriteEvaluationResult(
        name=rewriter.name,
        mode=mode,
        metrics=summary,
        query_details=details,
        rewrite_seconds=rewrite_seconds,
        search_seconds=search_seconds,
    )


def evaluate_rewriters(
    dataset: ChunkingDataset,
    searcher: LexicalSearcher,
    rewriters: Sequence[QueryRewriterLike],
    *,
    mode: str = "candidate",
    top_k: int = 10,
) -> list[RewriteEvaluationResult]:
    """在相同数据集和检索器上比较多个 Query Rewrite 策略。"""
    if not rewriters:
        raise ValueError("至少需要一个 Query Rewrite 策略")
    return [
        _evaluate_variant(dataset, searcher, rewriter, mode=mode, top_k=top_k)
        for rewriter in rewriters
    ]


def evaluate_understanding(
    dataset: ChunkingDataset,
    understanding: QueryUnderstandingLike,
) -> dict[str, Any]:
    """离线检查 Query Understanding 的覆盖率与结构化输出。"""
    details: list[dict[str, Any]] = []
    for query in dataset.queries:
        result = understanding.understand(query.query)
        details.append(
            {
                "query_id": query.query_id,
                "query": query.query,
                "expected_category": query.category,
                "parsed": result.model_dump(mode="json"),
                "filters": result.to_metadata_filters(),
            }
        )
    return {
        "strategy": understanding.name,
        "queries": len(details),
        "with_specific_intent": sum(
            item["parsed"]["intent"] != "general_search" for item in details
        ),
        "with_filters": sum(bool(item["filters"]) for item in details),
        "details": details,
    }


def _expected_filters_match(
    expected: dict[str, str | list[str]],
    actual: dict[str, str | list[str]],
) -> bool:
    """判断解析结果是否覆盖预期过滤条件，允许存在额外的可解释字段。"""
    for key, expected_value in expected.items():
        actual_value = actual.get(key)
        if isinstance(expected_value, list):
            if not isinstance(actual_value, list):
                return False
            if not all(value in actual_value for value in expected_value):
                return False
        elif actual_value != expected_value:
            return False
    return True


def evaluate_phase12_understanding(
    dataset: Phase12Dataset,
    understanding: QueryUnderstandingLike,
    *,
    mode: str = "candidate",
) -> dict[str, Any]:
    """评测 Phase 12 候选 / Gold Set 的 Query Understanding 投影。

    Phase 12 的 ``expected_intent`` 是业务描述，不是 Phase 15 的受控枚举，
    因此这里只报告结构化结果和预期过滤条件覆盖率，不伪造意图准确率。
    """
    if mode not in {"candidate", "formal"}:
        raise ValueError("mode 必须是 candidate 或 formal")
    if mode == "formal":
        not_confirmed = [
            query.query_id
            for query in dataset.queries
            if query.review_status != "confirmed"
        ]
        if not_confirmed:
            preview = ", ".join(not_confirmed[:5])
            suffix = "..." if len(not_confirmed) > 5 else ""
            raise ValueError(
                f"formal 模式要求全部 Query 为 confirmed；仍有 {len(not_confirmed)} 条未确认："
                f"{preview}{suffix}"
            )
    details: list[dict[str, Any]] = []
    with_expected_filters = 0
    matched_expected_filters = 0
    for query in dataset.queries:
        result = understanding.understand(query.query)
        actual_filters = result.to_metadata_filters()
        expected_filters = query.expected_filters
        matched: bool | None = None
        if expected_filters:
            with_expected_filters += 1
            matched = _expected_filters_match(expected_filters, actual_filters)
            matched_expected_filters += int(matched)
        details.append(
            {
                "query_id": query.query_id,
                "query": query.query,
                "category": query.category.value,
                "expected_intent": query.expected_intent,
                "expected_filters": expected_filters,
                "parsed": result.model_dump(mode="json"),
                "filters": actual_filters,
                "expected_filters_matched": matched,
                "review_status": query.review_status.value,
            }
        )
    return {
        "strategy": understanding.name,
        "dataset_kind": "phase12",
        "mode": mode,
        "queries": len(details),
        "with_specific_intent": sum(
            item["parsed"]["intent"] != "general_search" for item in details
        ),
        "with_filters": sum(bool(item["filters"]) for item in details),
        "with_expected_filters": with_expected_filters,
        "matched_expected_filters": matched_expected_filters,
        "expected_filter_match_rate": (
            matched_expected_filters / with_expected_filters
            if with_expected_filters
            else None
        ),
        "details": details,
    }


__all__ = [
    "QueryUnderstandingLike",
    "RewriteEvaluationResult",
    "evaluate_phase12_understanding",
    "evaluate_rewriters",
    "evaluate_understanding",
]
