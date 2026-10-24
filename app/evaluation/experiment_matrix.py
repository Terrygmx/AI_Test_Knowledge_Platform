"""Phase 27 最终实验矩阵：聚合已有实验结果为统一对比表。

矩阵中的每个格子只引用已有 manifest 的真实数字；缺失的实验显式标记
``missing``，不用估算或旧数字冒充。正式结论必须等 Phase 12 人工
Gold Set 重跑后再填。
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
        "source_phase": manifest.get("phase"),
        "mode": manifest.get("mode"),
        "formal_gold_set": manifest.get("formal_gold_set", False),
    }


def _find_result(manifest: dict[str, Any], names: set[str]) -> dict[str, Any] | None:
    for item in manifest.get("results", []):
        if item.get("name") in names:
            return item
    return None


def build_experiment_matrix(
    *,
    lexical: str | Path | None = None,
    hybrid: str | Path | None = None,
    retrieval: str | Path | None = None,
    query_rewrite: str | Path | None = None,
) -> dict[str, Any]:
    """构建计划中的实验矩阵；缺数据的行列显式标记 missing。

    计划要求的行：Vector / BM25 / Hybrid / Hybrid + Rerank /
    Hybrid + Query Rewrite / Final。当前仓库只有 BM25 / Hybrid /
    Query Rewrite 的 candidate 数据；Vector 与 Rerank 行来自
    Phase 9 / 11 的旧实验，若未提供 manifest 则保持 missing。
    """
    if all(value is None for value in (lexical, hybrid, retrieval, query_rewrite)):
        raise ValueError("至少提供一个 manifest 路径")
    rows: list[dict[str, Any]] = []

    if retrieval is not None:
        retrieval_manifest = _load(retrieval)
        for name, label in (("bm25", "bm25"), ("ilike", "ilike")):
            item = _find_result(retrieval_manifest, {name})
            if item is None:
                continue
            metrics = item.get("metrics", {})
            rows.append(
                {
                    "experiment": label,
                    "recall@5": metrics.get("recall@5"),
                    "mrr": metrics.get("mrr"),
                    "p95_seconds": item.get("query_p95_seconds"),
                    **_provenance(retrieval_manifest),
                }
            )

    if lexical is not None:
        lexical_manifest = _load(lexical)
        bm25 = _find_result(lexical_manifest, {"bm25"})
        if bm25 is not None and not any(row["experiment"] == "bm25" for row in rows):
            metrics = bm25.get("metrics", {})
            rows.append(
                {
                    "experiment": "bm25",
                    "recall@5": metrics.get("recall@5"),
                    "mrr": metrics.get("mrr"),
                    "p95_seconds": None,
                    **_provenance(lexical_manifest),
                }
            )

    if hybrid is not None:
        hybrid_manifest = _load(hybrid)
        for name, label in (("rrf", "hybrid"), ("rrf_rerank", "hybrid_rerank")):
            item = _find_result(hybrid_manifest, {name})
            if item is None:
                continue
            metrics = item.get("metrics", {})
            rows.append(
                {
                    "experiment": label,
                    "recall@5": metrics.get("recall@5"),
                    "mrr": metrics.get("mrr"),
                    "p95_seconds": None,
                    **_provenance(hybrid_manifest),
                }
            )

    if query_rewrite is not None:
        rewrite_manifest = _load(query_rewrite)
        for name, label in (("noop", "query_rewrite_noop"), ("deterministic", "query_rewrite")):
            item = _find_result(rewrite_manifest, {name})
            if item is None:
                continue
            metrics = item.get("metrics", {})
            rows.append(
                {
                    "experiment": label,
                    "recall@5": metrics.get("recall@5"),
                    "mrr": metrics.get("mrr"),
                    "p95_seconds": metrics.get("rewrite_seconds_p95"),
                    **_provenance(rewrite_manifest),
                }
            )

    planned = ["vector", "bm25", "hybrid", "hybrid_rerank", "query_rewrite", "final"]
    present = {row["experiment"] for row in rows}
    missing = [name for name in planned if name not in present]
    return {
        "rows": rows,
        "missing_experiments": missing,
        "note": (
            "所有数字均来自 candidate 模式 manifest；正式结论必须等"
            " Phase 12 人工 Gold Set 重跑后填写，final 行只能由人工评定"
        ),
    }


__all__ = ["build_experiment_matrix"]
