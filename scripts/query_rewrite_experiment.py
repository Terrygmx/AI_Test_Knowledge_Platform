#!/usr/bin/env python3
"""Phase 15/16 离线 Query Understanding + Rewrite 实验入口。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evaluation.dataset import load_dataset
from app.evaluation.lexical_experiment import BM25Searcher, retrieve_chunks
from app.evaluation.query_rewrite_evaluation import evaluate_rewriters, evaluate_understanding
from app.query.rewrite import DeterministicQueryRewriter, NoOpQueryRewriter
from app.query.understanding import DeterministicQueryUnderstanding

DEFAULT_DATASET = Path("eval/fixtures/phase8_simulated.json")
DEFAULT_OUTPUT = Path("results/query-rewrite-phase16.json")


def _git_output(arguments: list[str]) -> str | None:
    try:
        output = subprocess.check_output(
            ["git", *arguments],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return output or None


def _manifest(
    dataset: Any,
    results: list[Any],
    understanding_result: dict[str, Any],
    *,
    dataset_path: str | Path,
    mode: str,
    top_k: int,
    started_at: datetime,
    command: list[str],
) -> dict[str, Any]:
    return {
        "phase": 16,
        "manifest_schema_version": 1,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "git_commit": _git_output(["rev-parse", "HEAD"]) or "unknown",
        "git_commit_role": "generation",
        "git_dirty": bool(_git_output(["status", "--porcelain"])),
        "command": command,
        "mode": mode,
        "formal_gold_set": mode == "formal",
        "dataset": {
            "path": str(dataset_path),
            "sha256": dataset.dataset_sha256,
            "queries": len(dataset.queries),
        },
        "top_k": top_k,
        "understanding": understanding_result,
        "metric_definitions": {
            "recall_at_k": "Gold evidence hits in top-k / Gold evidence",
            "mrr": "mean reciprocal rank of first relevant result",
            "rewrite_seconds_p95": "nearest-rank P95 rewrite latency",
            "search_seconds_total": "sum of search latency",
        },
        "results": [
            {
                "name": result.name,
                "mode": result.mode,
                "metrics": result.metrics,
                "query_details": result.query_details,
            }
            for result in results
        ],
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Phase 16 Query Rewrite Evaluation")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--mode", choices=["candidate", "formal"], default="candidate")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.top_k < 1:
        parser.error("--top-k 必须 >= 1")
    if args.mode == "formal":
        parser.error(
            "Phase 16 formal 模式尚未接入人工确认的 Phase 12 Gold Set；"
            "当前只能使用 candidate 模式"
        )

    dataset = load_dataset(args.dataset)
    if args.dry_run:
        understanding = DeterministicQueryUnderstanding()
        sample = understanding.understand(dataset.queries[0].query)
        print(
            f"dataset={args.dataset} sha256={dataset.dataset_sha256[:12]} "
            f"queries={len(dataset.queries)} mode={args.mode} "
            f"intent={sample.intent} module={sample.module}"
        )
        return

    started_at = datetime.now(UTC)
    documents = retrieve_chunks(dataset)
    understanding = DeterministicQueryUnderstanding()
    rewriters = [
        NoOpQueryRewriter(),
        DeterministicQueryRewriter(),
    ]
    results = evaluate_rewriters(
        dataset,
        BM25Searcher(documents),
        rewriters,
        mode=args.mode,
        top_k=args.top_k,
    )
    manifest = _manifest(
        dataset,
        results,
        evaluate_understanding(dataset, understanding),
        dataset_path=args.dataset,
        mode=args.mode,
        top_k=args.top_k,
        started_at=started_at,
        command=[sys.executable, *sys.argv],
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for result in results:
        print(
            f"{result.name:12s} R@5={result.metrics.get('recall@5', 0):.3f} "
            f"MRR={result.metrics['mrr']:.3f} "
            f"changed={result.metrics['changed_queries']} "
            f"rewrite_p95={result.metrics['rewrite_seconds_p95']:.6f}s"
        )
    print(f"manifest={output}")


if __name__ == "__main__":
    main()
