"""Phase 22 版本解析、区间过滤与对比请求测试。"""

from __future__ import annotations

import pytest

from app.retrieval.versioning import VersionDiffRequest, VersionRange, parse_version


def test_parse_version_supports_two_and_three_segments() -> None:
    assert parse_version("v2.9") == (2, 9, 0)
    assert parse_version("v2.10.1") == (2, 10, 1)
    assert parse_version("V1.2") == (1, 2, 0)

    with pytest.raises(ValueError, match="版本号"):
        parse_version("2.9")
    with pytest.raises(ValueError, match="版本号"):
        parse_version("v2")


def test_version_ordering_is_numeric_not_lexicographic() -> None:
    assert parse_version("v2.10") > parse_version("v2.9")


def test_version_range_contains_with_inclusive_bounds() -> None:
    version_range = VersionRange(min_version="v2.3", max_version="v2.4")

    assert version_range.contains("v2.3") is True
    assert version_range.contains("v2.4") is True
    assert version_range.contains("v2.5") is False
    assert version_range.contains("v2.2") is False
    assert version_range.contains(None) is False
    assert version_range.contains("unknown") is False


def test_version_range_requires_valid_bounds() -> None:
    with pytest.raises(ValueError, match="边界"):
        VersionRange()
    with pytest.raises(ValueError, match="min_version"):
        VersionRange(min_version="v2.4", max_version="v2.3")


def test_version_diff_request_validates_versions() -> None:
    request = VersionDiffRequest(
        source_type="testcase",
        source_id="SHOP-LOGIN-001",
        base_version="v2.3",
        target_version="v2.4",
    )
    assert request.base_version == "v2.3"

    with pytest.raises(ValueError, match="版本号"):
        VersionDiffRequest(
            source_type="testcase",
            source_id="SHOP-LOGIN-001",
            base_version="2.3",
            target_version="v2.4",
        )
