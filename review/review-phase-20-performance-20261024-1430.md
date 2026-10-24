# Phase 20 Review：性能统计

- 日期：2026-10-24 14:30（Asia/Shanghai）
- 分支：`phase-12-evaluation-dataset`
- 结论：**分阶段延迟统计与并发结果契约已实现；真实压测仍受限于 PostgreSQL + 真实模型服务**

## 已实现

- 新增 `app/evaluation/performance.py`：
  - `percentile()` 采用 nearest-rank，空序列返回 0；
  - `LatencyRecorder` 按阶段记录样本，输出 count / total / mean /
    P50 / P95 / P99 / max，拒绝空白阶段与非法延迟；
  - `ConcurrencyResult` 记录并发探测的成功 / 失败与错误信息。
- Phase 18 的 `PipelineTrace.stage_seconds` 已提供分阶段原始数据，
  可直接喂给 `LatencyRecorder` 做多次请求聚合。

## 测试

- 新增 `tests/test_performance.py`：nearest-rank 分位数、多阶段汇总、
  非法样本拒绝、并发成功 / 失败记录；
- 全量 SQLite：653 passed, 17 skipped。

## 边界

- 未引入 Locust；当前统计是库级契约，压测调度和真实 P95 需要等
  Pipeline 接入 HTTP + PostgreSQL 后再跑；
- Pipeline 候选深度固定为 `top_k * 2`，正式吞吐测试前需要把候选深度
  纳入参数矩阵；
- 延迟数字不参与结果哈希，跨机器比较没有意义。
