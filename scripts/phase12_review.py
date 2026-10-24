#!/usr/bin/env python3
"""Phase 12 人工审阅辅助 CLI。

用法：
    # 1) 导出审阅 checklist（Terry 逐条填写 review.decision / review_notes）
    uv run python scripts/phase12_review.py export --output eval/fixtures/phase12_review_checklist.json

    # 2) 查看当前进度
    uv run python scripts/phase12_review.py progress

    # 3) 回填审阅结果（写到新文件，不覆盖候选原文件）
    uv run python scripts/phase12_review.py apply \
        --checklist eval/fixtures/phase12_review_checklist.json \
        --output eval/fixtures/phase12_goldset.json

回填后的文件仍需 `scripts/phase12_candidates.py --check --mode formal`
确认全部 confirmed 才能作为正式 Gold Set。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evaluation.phase12_dataset import Phase12DatasetError
from app.evaluation.phase12_review import (
    apply_review_decisions,
    export_review_checklist,
    review_progress,
)

DEFAULT_DATASET = "eval/fixtures/phase12_candidates.json"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Phase 12 人工审阅辅助")
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    subparsers = parser.add_subparsers(dest="command", required=True)

    export = subparsers.add_parser("export", help="导出审阅 checklist")
    export.add_argument("--output", required=True)

    subparsers.add_parser("progress", help="查看审阅进度")

    apply_cmd = subparsers.add_parser("apply", help="回填审阅结果")
    apply_cmd.add_argument("--checklist", required=True)
    apply_cmd.add_argument("--output", required=True)
    apply_cmd.add_argument(
        "--mode",
        choices=["candidate", "formal"],
        default="candidate",
        help="formal 要求回填后全部 confirmed",
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            payload = export_review_checklist(args.dataset, output=args.output)
            print(
                f"checklist 已导出：{args.output} items={payload['total']} "
                f"dataset_sha256={payload['dataset_sha256'][:12]}"
            )
        elif args.command == "progress":
            print(json.dumps(review_progress(args.dataset), ensure_ascii=False, indent=2))
        else:
            result = apply_review_decisions(
                args.dataset,
                args.checklist,
                output=args.output,
                mode=args.mode,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except Phase12DatasetError as exc:
        print(f"Phase 12 审阅失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
