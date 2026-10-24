#!/usr/bin/env python3
"""Phase 14 生成评测入口。

默认读取 Phase 12 候选集与离线模拟响应，验证 Faithfulness、Answer Relevancy、
Context Recall / Precision、Citation Accuracy 和 Badcase 归因。模拟响应只用于
工具链回归，不代表真实 LLM 质量；``formal`` 模式要求全部样本已人工确认。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evaluation.dataset import load_dataset
from app.evaluation.generation_evaluation import (
    GenerationCase,
    evaluate_generation,
)
from app.evaluation.lexical_experiment import retrieve_chunks
from app.evaluation.phase12_dataset import Phase12DatasetError, load_phase12_dataset
from app.schemas.rag import Citation, RAGResponse

DEFAULT_DATASET = Path("eval/fixtures/phase12_candidates.json")
DEFAULT_RESPONSES = Path("eval/fixtures/phase14_simulated_responses.json")
DEFAULT_CORPUS = Path("eval/fixtures/phase6_simulated.json")
DEFAULT_OUTPUT = Path("results/generation-phase14.json")


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


def _load_responses(path: str | Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("responses"), list):
        raise ValueError("响应夹具必须包含 responses 数组")
    responses: dict[str, dict[str, Any]] = {}
    for item in payload["responses"]:
        if not isinstance(item, dict) or not isinstance(item.get("query_id"), str):
            raise ValueError("每条模拟响应必须包含 query_id")
        query_id = item["query_id"]
        if query_id in responses:
            raise ValueError(f"模拟响应 query_id 重复：{query_id}")
        responses[query_id] = item
    return responses


def _contexts(
    query_id: str,
    context_sources: list[tuple[str, str, int]],
    corpus_texts: dict[tuple[str, str, int], str],
) -> list[str]:
    contexts: list[str] = []
    for source in context_sources:
        if source not in corpus_texts:
            raise ValueError(f"{query_id} 的 context_sources 引用了不存在的 Chunk：{source}")
        contexts.append(corpus_texts[source])
    return contexts


def build_cases(
    dataset_path: str | Path,
    responses_path: str | Path,
    corpus_path: str | Path,
    *,
    mode: str = "candidate",
) -> tuple[list[GenerationCase], list[RAGResponse]]:
    """把 Phase 12 数据与模拟响应组合成生成评测样本。"""
    phase12 = load_phase12_dataset(
        dataset_path,
        corpus=load_dataset(corpus_path),
        mode=mode,
    )
    corpus_texts = {
        (chunk.source_type, chunk.source_id, chunk.chunk_index): chunk.content
        for chunk in retrieve_chunks(load_dataset(corpus_path))
    }
    response_payloads = _load_responses(responses_path)
    cases: list[GenerationCase] = []
    responses: list[RAGResponse] = []
    known_query_ids = {query.query_id for query in phase12.queries}
    unknown_query_ids = set(response_payloads) - known_query_ids
    if unknown_query_ids:
        preview = ", ".join(sorted(unknown_query_ids)[:5])
        raise ValueError(f"响应夹具包含未知 Query：{preview}")

    for query in phase12.queries:
        payload = response_payloads.get(query.query_id)
        if payload is None:
            continue
        try:
            raw_sources = payload.get("context_sources", [])
            context_sources = [
                (item["source_type"], item["source_id"], item["chunk_index"])
                for item in raw_sources
            ]
            citations = [
                Citation(
                    source_type=item["source_type"],
                    source_id=item["source_id"],
                    chunk_index=item["chunk_index"],
                    snippet="",
                    rank=item["rank"],
                )
                for item in payload.get("citations", [])
            ]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"{query.query_id} 的响应上下文 / Citation 字段不完整") from exc
        answer = str(payload.get("answer", ""))
        cases.append(
            GenerationCase(
                query_id=query.query_id,
                question=query.query,
                contexts=_contexts(query.query_id, context_sources, corpus_texts),
                expected_answer_points=list(query.expected_answer_points),
                answerable=query.answerable,
                gold_sources=[
                    (item.source_type, item.source_id, item.chunk_index)
                    for item in query.gold_chunks
                ],
                gold_evidence=[
                    (
                        item.source_type,
                        item.source_id,
                        item.chunk_index,
                        item.evidence,
                    )
                    for item in query.gold_chunks
                ],
                context_sources=context_sources,
                reference_answer=payload.get("reference_answer"),
                category=query.category.value,
                review_status=query.review_status.value,
                reviewed_by=query.reviewed_by,
                reviewed_at=query.reviewed_at,
            )
        )
        responses.append(
            RAGResponse(
                question=query.query,
                answer=answer,
                citation_valid=bool(citations) or "证据不足" in answer,
                citations=citations,
                retrieved_count=len(context_sources),
            )
        )

    if not cases:
        raise ValueError("模拟响应没有匹配任何 Phase 12 Query")
    missing_query_ids = known_query_ids - set(response_payloads)
    if mode == "formal" and missing_query_ids:
        preview = ", ".join(sorted(missing_query_ids)[:5])
        raise ValueError(
            f"formal 模式必须覆盖全部 Query；仍缺少 {len(missing_query_ids)} 条：{preview}"
        )
    return cases, responses


def _manifest(
    *,
    result: Any,
    dataset_path: str | Path,
    responses_path: str | Path,
    corpus_path: str | Path,
    started_at: datetime,
    finished_at: datetime,
    command: list[str],
) -> dict[str, Any]:
    response_payload = json.loads(Path(responses_path).read_text(encoding="utf-8"))
    statuses = {"pending": 0, "confirmed": 0, "rejected": 0}
    for case in result.cases:
        statuses[case["review_status"]] = statuses.get(case["review_status"], 0) + 1

    return {
        "phase": 14,
        "manifest_schema_version": 1,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "git_commit": _git_output(["rev-parse", "HEAD"]) or "unknown",
        "git_commit_role": "generation",
        "git_dirty": bool(_git_output(["status", "--porcelain"])),
        "command": command,
        "mode": result.mode,
        "formal_gold_set": result.formal_gold_set,
        "evaluator": result.evaluator,
        "response_source": response_payload.get("response_source", "unknown"),
        "dataset": {
            "path": str(dataset_path),
            "sha256": result.dataset_sha256,
            "hash_scope": "evaluated_cases",
            "cases": len(result.cases),
            "review_statuses": statuses,
        },
        "corpus": {
            "path": str(corpus_path),
            "sha256": load_dataset(corpus_path).dataset_sha256,
        },
        "responses": {
            "path": str(responses_path),
            "sha256": hashlib.sha256(Path(responses_path).read_bytes()).hexdigest(),
        },
        "metric_definitions": {
            "context_recall": "Gold Chunk identities with matching evidence / Gold Chunks",
            "context_precision": "context Chunks with matching identity and evidence / context Chunks",
            "faithfulness": "answer sentences supported by context / answer sentences",
            "answer_relevancy": "0.5 * question-token overlap + 0.5 * answer-point coverage",
            "citation_accuracy": "valid citation numbers / all citation numbers",
            "refusal_correct": "unanswerable refused or answerable not refused",
            "unsupported_sentence_total": "total unsupported answer sentences across cases",
            "unsupported_sentence_rate": "unsupported sentences / evaluated cases",
        },
        "metrics": result.metrics,
        "cases": result.cases,
        "badcases": result.badcases,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Phase 14 Generation Evaluation")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--responses", default=str(DEFAULT_RESPONSES))
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--mode", choices=["candidate", "formal"], default="candidate")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        cases, responses = build_cases(
            args.dataset,
            args.responses,
            args.corpus,
            mode=args.mode,
        )
        expected_case_count = (
            len(
                load_phase12_dataset(
                    args.dataset,
                    corpus=load_dataset(args.corpus),
                    mode=args.mode,
                ).queries
            )
            if args.mode == "formal"
            else None
        )
        result = evaluate_generation(
            cases,
            responses,
            mode=args.mode,
            expected_case_count=expected_case_count,
        )
    except (Phase12DatasetError, ValueError) as exc:
        print(f"Phase 14 生成评测失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    started_at = datetime.now(UTC)
    if args.dry_run:
        print(
            f"dataset={args.dataset} cases={len(cases)} "
            f"mode={args.mode} evaluator={result.evaluator} "
            f"sha256={result.dataset_sha256[:12]}"
        )
        return

    manifest = _manifest(
        result=result,
        dataset_path=args.dataset,
        responses_path=args.responses,
        corpus_path=args.corpus,
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
    metrics = result.metrics
    print(
        f"cases={metrics['cases']} badcases={metrics['badcase_count']} "
        f"faithfulness={metrics['faithfulness']:.3f} "
        f"answer_relevancy={metrics['answer_relevancy']:.3f} "
        f"citation_accuracy={metrics['citation_accuracy']:.3f}"
    )
    print(f"manifest={output}")


if __name__ == "__main__":
    main()
