"""检索配置的 fail-fast 校验。"""

from __future__ import annotations

import pytest

from app.core.config import Settings


@pytest.mark.parametrize(
    ("field", "value", "expected_message"),
    [
        ("retrieval_vector_top_k", 0, "RETRIEVAL_VECTOR_TOP_K"),
        ("retrieval_keyword_top_k", 0, "RETRIEVAL_KEYWORD_TOP_K"),
        ("retrieval_final_top_k", 0, "RETRIEVAL_FINAL_TOP_K"),
        ("retrieval_rrf_k", 0, "RETRIEVAL_RRF_K"),
    ],
)
def test_retrieval_top_k_configuration_must_be_positive(
    field: str,
    value: int,
    expected_message: str,
) -> None:
    with pytest.raises(ValueError, match=expected_message):
        Settings(_env_file=None, **{field: value})


@pytest.mark.parametrize(
    "field",
    ["retrieval_vector_top_k", "retrieval_keyword_top_k"],
)
def test_final_top_k_cannot_exceed_either_channel_depth(field: str) -> None:
    with pytest.raises(ValueError, match="RETRIEVAL_FINAL_TOP_K"):
        Settings(_env_file=None, retrieval_final_top_k=6, **{field: 5})
