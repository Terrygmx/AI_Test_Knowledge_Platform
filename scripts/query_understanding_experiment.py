#!/usr/bin/env python3
"""Phase 15 Query Understanding 候选评测入口。

默认使用 Phase 12 的 100 条 AI 候选 Query。候选集全部是 ``pending``，因此
输出只能是 ``candidate`` 的覆盖率报告，不是人工确认后的意图准确率结论。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evaluation.phase12_dataset import (
    Phase12Dataset,
    Phase12DatasetError,
    load_phase12_dataset,
)
from app.evaluation.query_rewrite_evaluation import evaluate_phase12_understanding
from app.query.understanding import DeterministicQueryUnderstanding

DEFAULT_DATASET = Path("eval/fixtures/phase12_candidates.json")
DEFAULT_OUTPUT = Path("results/query-understanding-phase12.json")


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
    dataset: Phase12Dataset,
    report: dict[str, Any],
    *,
    dataset_path: str | Path,
    mode: str,
    started_at: datetime,
    command: list[str],
) -> dict[str, Any]:
    category_counts = Counter(query.category.value for query in dataset.queries)
    review_statuses = Counter(query.review_status.value for query in dataset.queries)
    return {
        "phase": 15,
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
            "corpus_dataset": dataset.corpus_dataset,
            "categories": dict(sorted(category_counts.items())),
            "review_statuses": dict(sorted(review_statuses.items())),
        },
        "understanding": report,
        "metric_definitions": {
            "with_specific_intent": "parsed intent != general_search",
            "with_filters": "projected Metadata Filters are non-empty",
            "expected_filter_match_rate": (
                "queries with expected_filters where all expected conditions are covered"
            ),
        },
        "limitations": [
            "Phase 12 expected_intent is business text, not a controlled enum; intent accuracy is not reported.",
            "Candidate rows are pending and must not be used as a formal Gold Set.",
            "Deterministic parser coverage is not a substitute for human semantic review.",
        ],
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Phase 15 Query Understanding Evaluation")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--mode", choices=["candidate", "formal"], default="candidate")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        dataset = load_phase12_dataset(args.dataset, mode=args.mode)
    except (Phase12DatasetError, ValueError, OSError) as exc:
        print(f"Phase 15 数据集校验失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    understanding = DeterministicQueryUnderstanding()
    report = evaluate_phase12_understanding(dataset, understanding, mode=args.mode)
    if args.dry_run:
        print(
            f"dataset={args.dataset} sha256={dataset.dataset_sha256[:12]} "
            f"queries={len(dataset.queries)} mode={args.mode} "
            f"filters={report['with_filters']}/{report['queries']} "
            f"expected_filter_match={report['matched_expected_filters']}/"
            f"{report['with_expected_filters']}"
        )
        return

    started_at = datetime.now(UTC)
    manifest = _manifest(
        dataset,
        report,
        dataset_path=args.dataset,
        mode=args.mode,
        started_at=started_at,
        command=[sys.executable, *sys.argv],
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"queries={report['queries']} "
        f"with_specific_intent={report['with_specific_intent']} "
        f"with_filters={report['with_filters']} "
        f"expected_filter_match={report['matched_expected_filters']}/"
        f"{report['with_expected_filters']} "
        f"manifest={output}"
    )


if __name__ == "__main__":
    main()
