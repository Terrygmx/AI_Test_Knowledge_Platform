"""Phase 9 混合检索融合逻辑测试。"""

from __future__ import annotations

import math

import pytest

from app.retrieval.hybrid import ChannelHit, fuse


def _hit(
    source_type: str,
    source_id: str,
    chunk_index: int,
    score: float,
    *,
    higher_is_better: bool = True,
    matched_terms: tuple[str, ...] = (),
) -> ChannelHit:
    return ChannelHit(
        source_type=source_type,
        source_id=source_id,
        chunk_index=chunk_index,
        score=score,
        content=f"{source_id}#{chunk_index}",
        higher_is_better=higher_is_better,
        matched_terms=matched_terms,
    )


def test_weighted_converts_distance_to_similarity_before_fusion() -> None:
    vector = [
        _hit("document", "DOC-1", 0, 0.1, higher_is_better=False),
        _hit("document", "DOC-2", 0, 0.9, higher_is_better=False),
    ]
    keyword = [
        _hit("document", "DOC-2", 0, 10.0),
        _hit("document", "DOC-1", 0, 1.0),
    ]

    result = fuse(
        vector,
        keyword,
        strategy="weighted",
        vector_weight=0.5,
        keyword_weight=0.5,
    )

    assert [item.source_id for item in result] == ["DOC-1", "DOC-2"]
    assert result[0].vector_rank == 1
    assert result[0].keyword_rank == 2
    assert result[0].vector_score == 0.1
    assert result[0].keyword_score == 1.0
    assert result[0].score == pytest.approx(0.5)
    assert result[1].score == pytest.approx(0.5)


def test_rrf_uses_rank_not_raw_score_magnitude() -> None:
    vector = [
        _hit("document", "DOC-1", 0, 0.001, higher_is_better=False),
        _hit("document", "DOC-2", 0, 0.999, higher_is_better=False),
    ]
    keyword = [
        _hit("document", "DOC-2", 0, 1.0),
        _hit("document", "DOC-1", 0, 1000.0),
    ]

    result = fuse(vector, keyword, strategy="rrf", rrf_k=60)

    assert [item.source_id for item in result] == ["DOC-1", "DOC-2"]
    assert result[0].score == pytest.approx(1 / 61 + 1 / 62)
    assert result[1].score == pytest.approx(1 / 62 + 1 / 61)


def test_duplicate_candidates_are_deduplicated_by_chunk_identity() -> None:
    vector = [
        _hit("testcase", "TC-1", 0, 0.1, higher_is_better=False),
        _hit("testcase", "TC-1", 0, 0.2, higher_is_better=False),
        _hit("testcase", "TC-1", 1, 0.3, higher_is_better=False),
    ]
    keyword = [_hit("testcase", "TC-1", 0, 2.0)]

    result = fuse(vector, keyword, strategy="rrf")

    assert [item.chunk_index for item in result] == [0, 1]
    assert result[0].vector_rank == 1


def test_one_channel_can_be_empty() -> None:
    keyword = [_hit("defect", "BUG-1", 0, 4.0)]

    result = fuse([], keyword, strategy="weighted")

    assert len(result) == 1
    assert result[0].source_id == "BUG-1"
    assert result[0].vector_rank is None
    assert result[0].keyword_rank == 1


@pytest.mark.parametrize(
    ("higher_is_better", "expected_order"),
    [
        (False, ["DOC-1", "DOC-2"]),
        (True, ["DOC-2", "DOC-1"]),
    ],
)
def test_weighted_honors_each_channel_declared_direction(
    higher_is_better: bool,
    expected_order: list[str],
) -> None:
    vector = [
        _hit("document", "DOC-1", 0, 0.1, higher_is_better=higher_is_better),
        _hit("document", "DOC-2", 0, 0.9, higher_is_better=higher_is_better),
    ]

    result = fuse(
        vector,
        [],
        strategy="weighted",
        vector_weight=1.0,
        keyword_weight=0.0,
    )

    assert [item.source_id for item in result] == expected_order


@pytest.mark.parametrize("strategy", ["weighted", "rrf"])
def test_mixed_score_directions_within_one_channel_are_rejected(strategy: str) -> None:
    vector = [
        _hit("document", "DOC-1", 0, 0.1, higher_is_better=False),
        _hit("document", "DOC-2", 0, 0.9, higher_is_better=True),
    ]

    with pytest.raises(ValueError, match="方向"):
        fuse(vector, [], strategy=strategy)


def test_fusion_keeps_matched_terms_and_content_for_explanation() -> None:
    keyword = [
        _hit(
            "defect",
            "BUG-1234",
            0,
            9.0,
            matched_terms=("bug-1234", "login-api"),
        )
    ]

    result = fuse([], keyword, strategy="rrf")

    assert result[0].matched_terms == ("bug-1234", "login-api")
    assert result[0].content == "BUG-1234#0"


def test_limit_applies_after_fusion() -> None:
    vector = [_hit("document", f"DOC-{i}", 0, i / 10, higher_is_better=False) for i in range(5)]
    keyword = [_hit("document", f"DOC-{i}", 0, 5 - i) for i in range(5)]

    result = fuse(vector, keyword, strategy="rrf", limit=2)

    assert len(result) == 2


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"strategy": "unknown"}, "strategy"),
        ({"rrf_k": 0}, "rrf_k"),
        ({"limit": 0}, "limit"),
        ({"vector_weight": -1}, "权重"),
        ({"vector_weight": 0, "keyword_weight": 0}, "至少"),
        ({"vector_weight": math.nan}, "有限"),
        ({"keyword_weight": math.inf}, "有限"),
    ],
)
def test_invalid_fusion_configuration_is_rejected(kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        fuse([], [], **kwargs)


@pytest.mark.parametrize(
    ("vector_hits", "keyword_hits"),
    [
        ([_hit("document", "DOC-1", 0, math.nan, higher_is_better=False)], []),
        ([], [_hit("document", "DOC-1", 0, math.inf)]),
    ],
)
def test_non_finite_channel_scores_are_rejected(vector_hits, keyword_hits) -> None:
    with pytest.raises(ValueError, match="有限"):
        fuse(vector_hits, keyword_hits, strategy="rrf")
