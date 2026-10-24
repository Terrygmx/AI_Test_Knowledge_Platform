"""Phase 19 Badcase 分类、闭环与回归绑定测试。"""

from __future__ import annotations

import pytest

from app.evaluation.badcase import Badcase, BadcaseStage, BadcaseStatus, BadcaseStore


def _badcase(**overrides) -> Badcase:
    payload = dict(
        badcase_id="BC-001",
        query_id="P12-TC-001",
        stage=BadcaseStage.RETRIEVAL,
        symptom="Top-5 未命中 Gold Chunk",
        root_cause="BM25 未保留版本号整串",
        hypothesis="把版本号加入保留标识符列表后复测",
        regression_query_ids=["P12-TC-001"],
    )
    payload.update(overrides)
    return Badcase.model_validate(payload)


def test_badcase_requires_regression_query() -> None:
    with pytest.raises(ValueError, match="回归"):
        _badcase(regression_query_ids=[])


def test_badcase_lifecycle_requires_evaluation_before_verified() -> None:
    item = _badcase()

    item.advance()
    assert item.status is BadcaseStatus.FIXING
    item.advance()
    assert item.status is BadcaseStatus.FIXED
    with pytest.raises(ValueError, match="evaluation_result"):
        item.advance()

    item.advance(evaluation_result={"recall@5": 1.0})
    assert item.status is BadcaseStatus.VERIFIED
    assert item.evaluation_result == {"recall@5": 1.0}
    with pytest.raises(ValueError, match="验证完成"):
        item.advance()


def test_verified_directly_rejected_without_evaluation() -> None:
    with pytest.raises(ValueError, match="evaluation_result"):
        _badcase(status=BadcaseStatus.VERIFIED)


def test_store_deduplicates_and_filters() -> None:
    store = BadcaseStore()
    store.add(_badcase())
    store.add(_badcase(badcase_id="BC-002", stage=BadcaseStage.GENERATION))

    with pytest.raises(ValueError, match="重复"):
        store.add(_badcase())
    assert len(store.list()) == 2
    assert len(store.list(stage=BadcaseStage.RETRIEVAL)) == 1
    assert store.open_count() == 2

    with pytest.raises(KeyError):
        store.get("missing")
