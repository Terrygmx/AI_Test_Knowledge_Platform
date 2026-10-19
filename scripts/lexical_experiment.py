#!/usr/bin/env python3
"""Phase 8 BM25 / 词法检索实验。

示例：
    uv run python scripts/lexical_experiment.py --dry-run
    uv run python scripts/lexical_experiment.py \
        --output results/lexical-phase8.json

实验不访问数据库或模型服务：把 ``entity+heading`` Chunk 读入内存，比较当前
``ILIKE`` 基线与 BM25。manifest 记录数据哈希、参数、Git 状态与逐类指标。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evaluation.dataset import ChunkingDataset, load_dataset
from app.evaluation.lexical_experiment import (
    BM25Searcher,
    ILikeSearcher,
    LexicalResult,
    demonstrate_baseline_failure,
    evaluate_searcher,
    k_values_for,
    retrieve_chunks,
)

DEFAULT_DATASET = Path("eval/fixtures/phase8_simulated.json")


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
    dataset: ChunkingDataset,
    results: list[LexicalResult],
    *,
    dataset_path: str | Path,
    started_at: datetime,
    finished_at: datetime,
    command: list[str],
    k1: float,
    b: float,
    top_k: int,
    baseline: dict[str, object] | None = None,
) -> dict[str, Any]:
    if k1 <= 0:
        raise ValueError("k1 必须大于 0")
    if not 0 <= b <= 1:
        raise ValueError("b 必须在 [0, 1] 内")
    k_values = k_values_for(top_k)

    return {
        "phase": 8,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "git_commit": _git_output(["rev-parse", "HEAD"]) or "unknown",
        "git_dirty": bool(_git_output(["status", "--porcelain"])),
        "command": command,
        "top_k": top_k,
        "k_values": list(k_values),
        "dataset": {
            "path": str(dataset_path),
            "sha256": dataset.dataset_sha256,
            "queries": len(dataset.queries),
            "answerable": sum(query.answerable for query in dataset.queries),
            "unanswerable": sum(not query.answerable for query in dataset.queries),
        },
        "algorithm": {
            "name": "bm25",
            "k1": k1,
            "b": b,
            "tokenizer": "unicode-whole+cjk-unigram-bigram",
        },
        "results": [asdict(result) for result in results],
        "baseline_failure": baseline,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 8 BM25 词法检索实验")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--output", default="results/lexical-phase8.json")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--k1", type=float, default=1.2)
    parser.add_argument("--b", type=float, default=0.75)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.top_k < 1:
        parser.error("--top-k 必须 >= 1")
    if args.k1 <= 0:
        parser.error("--k1 必须 > 0")
    if not 0 <= args.b <= 1:
        parser.error("--b 必须在 [0, 1] 内")

    dataset = load_dataset(args.dataset)
    documents = retrieve_chunks(dataset)
    if args.dry_run:
        print(
            f"dataset={args.dataset} sha256={dataset.dataset_sha256[:12]} "
            f"queries={len(dataset.queries)} chunks={len(documents)} "
            f"tokenizer=unicode-whole+cjk-unigram-bigram k1={args.k1} b={args.b} "
            f"top_k={args.top_k} k_values={list(k_values_for(args.top_k))}"
        )
        return

    started_at = datetime.now(UTC)
    results = [
        evaluate_searcher(ILikeSearcher(documents), dataset, name="ilike", top_k=args.top_k),
        evaluate_searcher(
            BM25Searcher(documents, k1=args.k1, b=args.b),
            dataset,
            name="bm25",
            top_k=args.top_k,
        ),
    ]
    baseline = demonstrate_baseline_failure(documents)
    manifest = _manifest(
        dataset,
        results,
        dataset_path=args.dataset,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        command=[sys.executable, *sys.argv],
        k1=args.k1,
        b=args.b,
        top_k=args.top_k,
        baseline=baseline,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for result in results:
        recall = " ".join(
            f"R@{k}={result.metrics[f'recall@{k}']:.3f}" for k in result.k_values
        )
        print(
            f"{result.name:6s} {recall} MRR={result.metrics['mrr']:.3f} "
            f"p95={result.query_p95_seconds:.4f}s"
        )
    print(f"manifest={output}")


if __name__ == "__main__":
    main()
