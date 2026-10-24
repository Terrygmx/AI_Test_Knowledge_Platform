"""Phase 22 版本管理：Version-aware Retrieval 的过滤与对比语义。

版本号使用 ``v<major>.<minor>[.<patch>]`` 形式，比较时按数值而非字符串，
避免 ``v2.10`` 被排到 ``v2.9`` 之前。
"""

from __future__ import annotations

import re

from pydantic import Field, model_validator

from app.schemas.common import BaseSchema

_VERSION_PATTERN = re.compile(r"^v(\d+)\.(\d+)(?:\.(\d+))?$", re.IGNORECASE)


def parse_version(value: str) -> tuple[int, int, int]:
    """解析版本号；非法格式抛 ``ValueError``，不做猜测。"""
    match = _VERSION_PATTERN.match(value.strip())
    if not match:
        raise ValueError(f"版本号格式必须是 v<major>.<minor>[.<patch>]：{value!r}")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


class VersionRange(BaseSchema):
    """版本区间过滤；边界都按包含语义处理。"""

    min_version: str | None = None
    max_version: str | None = None

    @model_validator(mode="after")
    def _validate_bounds(self) -> VersionRange:
        if self.min_version is None and self.max_version is None:
            raise ValueError("版本区间至少需要一个边界")
        if self.min_version is not None:
            parse_version(self.min_version)
        if self.max_version is not None:
            parse_version(self.max_version)
        if (
            self.min_version is not None
            and self.max_version is not None
            and parse_version(self.min_version) > parse_version(self.max_version)
        ):
            raise ValueError("min_version 不能大于 max_version")
        return self

    def contains(self, version: str | None) -> bool:
        if version is None:
            return False
        try:
            parsed = parse_version(version)
        except ValueError:
            return False
        above_min = self.min_version is None or parsed >= parse_version(self.min_version)
        below_max = self.max_version is None or parsed <= parse_version(self.max_version)
        return above_min and below_max


class VersionDiffRequest(BaseSchema):
    """跨版本对比查询。"""

    source_type: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    base_version: str
    target_version: str

    @model_validator(mode="after")
    def _validate_versions(self) -> VersionDiffRequest:
        parse_version(self.base_version)
        parse_version(self.target_version)
        return self


__all__ = ["VersionDiffRequest", "VersionRange", "parse_version"]
