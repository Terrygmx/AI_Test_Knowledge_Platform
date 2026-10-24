"""Phase 20 性能统计：分阶段延迟与分位数。

只做纯统计，不做压测调度。调用方在自己的负载工具中重复调用
``record``，再用 ``summary`` 得到 P50 / P95 / P99；延迟数据不参与
结果哈希，只用于定位瓶颈。
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence


def percentile(values: Sequence[float], quantile: float) -> float:
    """nearest-rank 分位数；空序列返回 0.0。"""
    if not values:
        return 0.0
    if not 0 < quantile <= 1:
        raise ValueError("quantile 必须在 (0, 1]")
    ordered = sorted(values)
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[rank - 1]


class LatencyRecorder:
    """按阶段记录延迟样本。"""

    def __init__(self) -> None:
        self._samples: dict[str, list[float]] = defaultdict(list)

    def record(self, stage: str, seconds: float) -> None:
        if not stage.strip():
            raise ValueError("stage 不能为空白")
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("延迟必须是有限的非负数")
        self._samples[stage].append(seconds)

    def summary(self) -> dict[str, dict[str, float | int]]:
        result: dict[str, dict[str, float | int]] = {}
        for stage, values in sorted(self._samples.items()):
            result[stage] = {
                "count": len(values),
                "total": sum(values),
                "mean": sum(values) / len(values),
                "p50": percentile(values, 0.50),
                "p95": percentile(values, 0.95),
                "p99": percentile(values, 0.99),
                "max": max(values),
            }
        return result


class ConcurrencyResult:
    """一次并发探测的成功 / 失败计数。"""

    def __init__(self) -> None:
        self.successes = 0
        self.failures = 0
        self.errors: list[str] = []

    def record_success(self) -> None:
        self.successes += 1

    def record_failure(self, error: str) -> None:
        self.failures += 1
        self.errors.append(error)

    @property
    def all_succeeded(self) -> bool:
        return self.failures == 0 and self.successes > 0


__all__ = ["ConcurrencyResult", "LatencyRecorder", "percentile"]
