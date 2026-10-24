"""Phase 20 性能统计与并发探测测试。"""

from __future__ import annotations

import pytest

from app.evaluation.performance import ConcurrencyResult, LatencyRecorder, percentile


def test_percentile_uses_nearest_rank() -> None:
    values = [1.0, 2.0, 3.0, 4.0]

    assert percentile(values, 0.5) == 2.0
    assert percentile(values, 0.95) == 4.0
    assert percentile(values, 1.0) == 4.0
    assert percentile([], 0.5) == 0.0
    with pytest.raises(ValueError, match="quantile"):
        percentile(values, 0)


def test_latency_recorder_summarizes_stages() -> None:
    recorder = LatencyRecorder()
    for value in (0.1, 0.2, 0.3, 0.4):
        recorder.record("llm", value)
    recorder.record("embedding", 0.05)

    summary = recorder.summary()
    assert summary["llm"]["count"] == 4
    assert summary["llm"]["p50"] == pytest.approx(0.2)
    assert summary["llm"]["p95"] == pytest.approx(0.4)
    assert summary["llm"]["max"] == pytest.approx(0.4)
    assert summary["embedding"]["count"] == 1


def test_latency_recorder_rejects_invalid_samples() -> None:
    recorder = LatencyRecorder()

    with pytest.raises(ValueError, match="stage"):
        recorder.record("  ", 0.1)
    with pytest.raises(ValueError, match="延迟"):
        recorder.record("llm", -1.0)
    with pytest.raises(ValueError, match="延迟"):
        recorder.record("llm", float("nan"))


def test_concurrency_result_tracks_success_and_failure() -> None:
    result = ConcurrencyResult()
    result.record_success()
    result.record_success()

    assert result.all_succeeded is True

    result.record_failure("timeout")
    assert result.all_succeeded is False
    assert result.failures == 1
    assert result.errors == ["timeout"]
