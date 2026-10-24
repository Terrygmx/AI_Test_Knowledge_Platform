"""Phase 12 审阅辅助工具测试：checklist 导出、回填与校验兜底。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.evaluation.dataset import load_dataset
from app.evaluation.phase12_dataset import Phase12DatasetError, load_phase12_dataset
from app.evaluation.phase12_review import (
    apply_review_decisions,
    export_review_checklist,
    review_progress,
)

DATASET = "eval/fixtures/phase12_candidates.json"
CORPUS = "eval/fixtures/phase6_simulated.json"


def test_export_checklist_contains_all_items_with_review_block(tmp_path: Path) -> None:
    output = tmp_path / "checklist.json"
    payload = export_review_checklist(DATASET, output=output)

    assert payload["total"] == 100
    assert payload["reviewer"] == "Terry"
    assert output.exists()
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert len(saved["items"]) == 100
    first = saved["items"][0]
    assert first["review"] == {"decision": "", "review_notes": "", "reviewed_by": "Terry"}
    # 可回答条目带 chunk 正文，供审阅人核对证据
    assert first["gold_chunks"][0]["chunk_content"]


def test_apply_writes_reviewed_copy_without_touching_source(tmp_path: Path) -> None:
    checklist = export_review_checklist(DATASET)
    for item in checklist["items"][:3]:
        item["review"]["decision"] = "confirmed"
        item["review"]["review_notes"] = "证据与来源核对一致"
    checklist_path = tmp_path / "checklist.json"
    checklist_path.write_text(json.dumps(checklist, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "reviewed.json"
    before = Path(DATASET).read_text(encoding="utf-8")

    result = apply_review_decisions(DATASET, checklist_path, output=output)

    assert result["changed"] == 3
    assert result["confirmed"] == 3
    assert result["pending"] == 97
    # 候选原文件未被覆盖
    assert Path(DATASET).read_text(encoding="utf-8") == before
    reviewed = load_phase12_dataset(output, corpus=load_dataset(CORPUS), mode="candidate")
    statuses = {q.query_id: q.review_status.value for q in reviewed.queries}
    assert sum(value == "confirmed" for value in statuses.values()) == 3
    confirmed = [q for q in reviewed.queries if q.review_status.value == "confirmed"][0]
    assert confirmed.reviewed_by == "Terry"
    assert confirmed.reviewed_at is not None
    assert confirmed.reviewed_at.tzinfo is not None
    assert confirmed.review_notes == "证据与来源核对一致"


def test_apply_rejects_unknown_decision(tmp_path: Path) -> None:
    checklist = export_review_checklist(DATASET)
    checklist["items"][0]["review"]["decision"] = "maybe"
    checklist["items"][0]["review"]["review_notes"] = "x"
    path = tmp_path / "checklist.json"
    path.write_text(json.dumps(checklist, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(Phase12DatasetError, match="decision 非法"):
        apply_review_decisions(DATASET, path, output=tmp_path / "out.json")


def test_apply_rejects_missing_notes(tmp_path: Path) -> None:
    checklist = export_review_checklist(DATASET)
    checklist["items"][0]["review"]["decision"] = "confirmed"
    checklist["items"][0]["review"]["review_notes"] = ""
    path = tmp_path / "checklist.json"
    path.write_text(json.dumps(checklist, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(Phase12DatasetError, match="review_notes"):
        apply_review_decisions(DATASET, path, output=tmp_path / "out.json")


def test_apply_formal_mode_requires_all_confirmed(tmp_path: Path) -> None:
    checklist = export_review_checklist(DATASET)
    for item in checklist["items"]:
        item["review"]["decision"] = "confirmed"
        item["review"]["review_notes"] = "核对一致"
    # 只回填一半，formal 必须拒绝
    for item in checklist["items"][50:]:
        item["review"]["decision"] = ""
    path = tmp_path / "checklist.json"
    path.write_text(json.dumps(checklist, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(Phase12DatasetError, match="confirmed"):
        apply_review_decisions(
            DATASET, path, output=tmp_path / "out.json", mode="formal"
        )


def test_progress_counts_pending() -> None:
    progress = review_progress(DATASET)

    assert progress["total"] == 100
    assert progress["pending"] == 100
    assert progress["confirmed"] == 0
