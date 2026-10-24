#!/usr/bin/env python3
"""Phase 13 检索评测入口。

默认在 Phase 8 的离线语料上比较 ILIKE 与 BM25，输出 Recall@K、Precision@K、
MRR、NDCG@K 与逐 Query Badcase 归因。也可用 ``--phase12-dataset`` 对 Phase 12
数据运行探索性评测；未人工确认的数据只能以 ``candidate`` 模式运行。
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
    retrieve_chunks,
)
from app.evaluation.phase12_dataset import (
    Phase12DatasetError,
    load_phase12_dataset,
    phase12_to_chunking_dataset,
)
from app.evaluation.retrieval_evaluation import (
    RetrievalEvaluationResult,
    evaluate_retriever,
    retrieval_result_sha256,
)

DEFAULT_DATASET = Path("eval/fixtures/phase8_simulated.json")
DEFAULT_PHASE12_DATASET = Path("eval/fixtures/phase12_candidates.json")
DEFAULT_CORPUS = Path("eval/fixtures/phase6_simulated.json")


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
    results: list[RetrievalEvaluationResult],
    *,
    dataset_path: str | Path,
    corpus_path: str | Path | None,
    mode: str,
    started_at: datetime,
    finished_at: datetime,
    command: list[str],
) -> dict[str, Any]:
    return {
        "phase": 13,
        "manifest_schema_version": 1,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
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
            "answerable": sum(query.answerable for query in dataset.queries),
            "unanswerable": sum(not query.answerable for query in dataset.queries),
        },
        "corpus": {
            "path": str(corpus_path) if corpus_path is not None else None,
            "sha256": load_dataset(corpus_path).dataset_sha256 if corpus_path is not None else None,
        },
        "metric_definitions": {
            "recall_at_k": "hit gold labels in top-k / total gold labels",
            "precision_at_k": "hit gold labels in top-k / actual returned count",
            "mrr": "mean reciprocal rank of first relevant result",
            "ndcg_at_k": "binary-gain DCG / IDCG at k",
            "badcase": "retrieval-only attribution: empty_results / wrong_source / "
            "no_gold_hit / ranked_below_k / unexpected_results",
        },
        "results": [
            {
                **asdict(result),
                "result_sha256": retrieval_result_sha256(result),
            }
            for result in results
        ],
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Phase 13 Retrieval Evaluation")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--phase12-dataset", default=None)
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--output", default="results/retrieval-phase13.json")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--mode", choices=["candidate", "formal"], default="candidate")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.top_k < 1:
        parser.error("--top-k 必须 >= 1")

    phase12 = None
    if args.phase12_dataset:
        try:
            phase12 = load_phase12_dataset(
                args.phase12_dataset,
                corpus=load_dataset(args.corpus),
                mode=args.mode,
            )
        except Phase12DatasetError as exc:
            print(f"Phase 13 数据集校验失败：{exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        corpus = load_dataset(args.corpus)
        dataset = phase12_to_chunking_dataset(phase12, corpus)
        dataset_path = args.phase12_dataset
    else:
        dataset = load_dataset(args.dataset)
        corpus = None
        dataset_path = args.dataset

    documents = retrieve_chunks(dataset)
    if args.dry_run:
        print(
            f"dataset={dataset_path} sha256={dataset.dataset_sha256[:12]} "
            f"queries={len(dataset.queries)} chunks={len(documents)} "
            f"top_k={args.top_k} mode={args.mode}"
        )
        return

    started_at = datetime.now(UTC)
    results = [
        evaluate_retriever(
            dataset,
            ILikeSearcher(documents),
            name="ilike",
            mode=args.mode,
            top_k=args.top_k,
        ),
        evaluate_retriever(
            dataset,
            BM25Searcher(documents),
            name="bm25",
            mode=args.mode,
            top_k=args.top_k,
        ),
    ]
    manifest = _manifest(
        dataset,
        results,
        dataset_path=dataset_path,
        corpus_path=args.corpus if phase12 is not None else None,
        mode=args.mode,
        started_at=started_at,
        finished_at=datetime.now(UTC),
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
            f"{result.name:6s} R@5={result.metrics.get('recall@5', 0):.3f} "
            f"P@5={result.metrics.get('precision@5', 0):.3f} "
            f"MRR={result.metrics['mrr']:.3f} "
            f"NDCG@5={result.metrics.get('ndcg@5', 0):.3f} "
            f"badcases={result.metrics['badcase_count']}"
        )
    print(f"manifest={output}")


if __name__ == "__main__":
    main()
