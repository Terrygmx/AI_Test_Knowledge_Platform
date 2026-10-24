"""Phase 12 人工审阅辅助：checklist 导出与审阅结果回填。

边界（与 `phase12_dataset` 契约一致）：

- 工具只做「展示候选 + 写回 Terry 的审阅决定」，**不代替人工判断**；
- 回填时逐条构造 `Phase12Query` 复用全部校验（审阅人、时区、备注、
  pending 不带元数据），并重新执行语料校验，防止审阅过程中破坏证据链；
- 回填输出默认写到新文件，不覆盖候选原文件。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app.evaluation.dataset import ChunkingDataset, load_dataset
from app.evaluation.phase12_dataset import (
    REVIEWED_BY,
    Phase12Dataset,
    Phase12DatasetError,
    Phase12Query,
    ReviewStatus,
    load_phase12_dataset,
    validate_phase12_dataset,
)

__all__ = [
    "REVIEWER",
    "export_review_checklist",
    "apply_review_decisions",
    "review_progress",
]

REVIEWER = REVIEWED_BY


def _corpus_for(dataset_path: Path, dataset: Phase12Dataset) -> ChunkingDataset:
    from app.evaluation.phase12_dataset import _resolve_corpus_path

    return load_dataset(_resolve_corpus_path(dataset_path, dataset.corpus_dataset))


def export_review_checklist(
    dataset_path: str | Path,
    *,
    output: str | Path | None = None,
) -> dict[str, Any]:
    """把候选数据集导出成逐条审阅 checklist。

    每条包含审阅人需要核对的信息：问题、意图、过滤条件、Gold 来源 /
    Chunk 与证据原文（从语料取正文并截取证据上下文）、答案要点、answerable。
    ``review`` 区是留给 Terry 填写的空白决定。
    """
    path = Path(dataset_path)
    dataset = load_phase12_dataset(path, mode="candidate")
    corpus = _corpus_for(path, dataset)

    from app.evaluation.phase12_dataset import _chunk_map

    chunks = _chunk_map(corpus)
    items: list[dict[str, Any]] = []
    for query in dataset.queries:
        chunks_detail = []
        for gold in query.gold_chunks:
            chunk = chunks.get((gold.source_type, gold.source_id, gold.chunk_index))
            chunks_detail.append(
                {
                    "source_type": gold.source_type,
                    "source_id": gold.source_id,
                    "chunk_index": gold.chunk_index,
                    "claimed_evidence": gold.evidence,
                    "chunk_content": chunk.content if chunk else None,
                }
            )
        items.append(
            {
                "query_id": query.query_id,
                "category": query.category.value,
                "query": query.query,
                "expected_intent": query.expected_intent,
                "expected_filters": query.expected_filters,
                "answerable": query.answerable,
                "expected_answer_points": query.expected_answer_points,
                "gold_documents": [item.model_dump() for item in query.gold_documents],
                "gold_chunks": chunks_detail,
                "review": {
                    "decision": "",  # confirmed / rejected
                    "review_notes": "",
                    "reviewed_by": REVIEWER,
                },
            }
        )

    payload: dict[str, Any] = {
        "purpose": (
            "Phase 12 人工审阅 checklist：逐条核对问题、意图、过滤条件、Gold 证据与答案要点；"
            "decision 填 confirmed 或 rejected，review_notes 必填（confirmed 说明核对依据，"
            "rejected 说明错误原因）。回填命令：python scripts/phase12_review.py apply"
        ),
        "dataset_path": str(path),
        "dataset_sha256": dataset.dataset_sha256,
        "total": len(items),
        "reviewer": REVIEWER,
        "items": items,
    }
    if output is not None:
        out = Path(output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _parse_decision_time(value: str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise Phase12DatasetError(f"reviewed_at 必须带时区：{value}")
    return parsed


def apply_review_decisions(
    dataset_path: str | Path,
    checklist_path: str | Path,
    *,
    output: str | Path | None = None,
    mode: Literal["candidate", "formal"] = "candidate",
) -> dict[str, Any]:
    """把 checklist 中的审阅决定写回数据集，并重新校验证据链。

    只更新出现在 checklist 里且 decision 非空的条目；其余条目保持原状。
    pending → 带审阅元数据的转换由 `Phase12Query` 契约层校验兜底。
    """
    path = Path(dataset_path)
    dataset = load_phase12_dataset(path, mode="candidate")
    checklist = json.loads(Path(checklist_path).read_text(encoding="utf-8"))

    decisions: dict[str, dict[str, Any]] = {}
    for item in checklist.get("items", []):
        decision = str(item.get("review", {}).get("decision", "") or "").strip().lower()
        if not decision:
            continue
        if decision not in {status.value for status in ReviewStatus}:
            raise Phase12DatasetError(
                f"{item.get('query_id')} 的 decision 非法：{decision!r}；"
                "只允许 confirmed / rejected"
            )
        decisions[str(item["query_id"])] = item["review"]

    updated: list[Phase12Query] = []
    changed = 0
    for query in dataset.queries:
        review = decisions.get(query.query_id)
        if review is None:
            updated.append(query)
            continue
        status = ReviewStatus(review["decision"].strip().lower())
        notes = str(review.get("review_notes", "") or "").strip()
        if not notes:
            raise Phase12DatasetError(f"{query.query_id} 缺少 review_notes")
        updated_query = query.model_copy(
            update={
                "review_status": status,
                "reviewed_by": REVIEWER,
                "reviewed_at": _parse_decision_time(review.get("reviewed_at")),
                "review_notes": notes,
            }
        )
        updated.append(updated_query)
        changed += 1

    new_dataset = dataset.model_copy(update={"queries": updated})
    corpus = _corpus_for(path, new_dataset)
    validate_phase12_dataset(new_dataset, corpus, mode=mode)

    payload = new_dataset.model_dump(mode="json")
    target = Path(output) if output is not None else path.with_name(
        path.stem + "-reviewed.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "output": str(target),
        "total": len(updated),
        "changed": changed,
        "confirmed": sum(q.review_status is ReviewStatus.CONFIRMED for q in updated),
        "rejected": sum(q.review_status is ReviewStatus.REJECTED for q in updated),
        "pending": sum(q.review_status is ReviewStatus.PENDING for q in updated),
        "dataset_sha256": new_dataset.dataset_sha256,
    }


def review_progress(dataset_path: str | Path) -> dict[str, int | str]:
    """当前审阅进度：各状态计数与数据集哈希。"""
    dataset = load_phase12_dataset(dataset_path, mode="candidate")
    return {
        "total": len(dataset.queries),
        "confirmed": sum(q.review_status is ReviewStatus.CONFIRMED for q in dataset.queries),
        "rejected": sum(q.review_status is ReviewStatus.REJECTED for q in dataset.queries),
        "pending": sum(q.review_status is ReviewStatus.PENDING for q in dataset.queries),
        "dataset_sha256": dataset.dataset_sha256,
    }
