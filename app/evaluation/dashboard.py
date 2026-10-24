"""Phase 24 Dashboard 数据聚合：从评测 manifest 提取核心指标。

只做纯数据投影，不做 UI。每个指标都携带来源 manifest 的 phase 与
``formal_gold_set`` 标志，防止把 candidate 数字当成正式结论展示。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _load(path: str | Path) -> dict[str, Any]:
    payload: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"manifest 必须是 JSON 对象：{path}")
    return payload


def _provenance(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "phase": manifest.get("phase"),
        "mode": manifest.get("mode"),
        "formal_gold_set": manifest.get("formal_gold_set", False),
    }


def retrieval_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    """从 Phase 13 manifest 提取各检索器核心指标。"""
    results = []
    for item in manifest.get("results", []):
        metrics = item.get("metrics", {})
        results.append(
            {
                "name": item.get("name"),
                "recall@5": metrics.get("recall@5"),
                "precision@5": metrics.get("precision@5"),
                "mrr": metrics.get("mrr"),
                "ndcg@5": metrics.get("ndcg@5"),
                "badcase_count": metrics.get("badcase_count"),
            }
        )
    return {"kind": "retrieval", **_provenance(manifest), "results": results}


def generation_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    """从 Phase 14 manifest 提取生成指标。"""
    metrics = manifest.get("metrics", {})
    return {
        "kind": "generation",
        **_provenance(manifest),
        "cases": metrics.get("cases"),
        "badcase_count": metrics.get("badcase_count"),
        "faithfulness": metrics.get("faithfulness"),
        "citation_accuracy": metrics.get("citation_accuracy"),
        "refusal_correct": metrics.get("refusal_correct"),
    }


def dataset_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    """从 Phase 15 manifest 提取候选集与理解覆盖。"""
    dataset = manifest.get("dataset", {})
    understanding = manifest.get("understanding", {})
    return {
        "kind": "dataset",
        **_provenance(manifest),
        "queries": dataset.get("queries"),
        "review_statuses": dataset.get("review_statuses"),
        "understanding_queries": understanding.get("queries"),
        "with_specific_intent": understanding.get("with_specific_intent"),
        "expected_filter_match_rate": understanding.get("expected_filter_match_rate"),
    }


def build_dashboard(
    *,
    retrieval: str | Path | None = None,
    generation: str | Path | None = None,
    dataset: str | Path | None = None,
) -> dict[str, Any]:
    """聚合多个 manifest；至少提供一个，缺失的块返回 None。"""
    if retrieval is None and generation is None and dataset is None:
        raise ValueError("至少提供一个 manifest 路径")
    return {
        "retrieval": retrieval_summary(_load(retrieval)) if retrieval else None,
        "generation": generation_summary(_load(generation)) if generation else None,
        "dataset": dataset_summary(_load(dataset)) if dataset else None,
    }


__all__ = ["build_dashboard", "dataset_summary", "generation_summary", "retrieval_summary"]
