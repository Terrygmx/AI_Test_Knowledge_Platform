# Phase 22 Review：版本管理

- 日期：2026-10-24 14:34（Asia/Shanghai）
- 分支：`phase-12-evaluation-dataset`
- 结论：**版本解析、区间过滤与对比请求契约已实现；Version-aware 检索执行器尚未接入**

## 已实现

- 新增 `app/retrieval/versioning.py`：
  - `parse_version()` 支持 `v<major>.<minor>[.<patch>]`，大小写不敏感，
    按数值比较（`v2.10 > v2.9`），非法格式直接失败；
  - `VersionRange` 提供包含式区间过滤；非法边界与反向区间被拒绝；
  - `VersionDiffRequest` 为跨版本对比查询提供受控契约。

## 测试

- 新增 `tests/test_versioning.py`：解析、数值排序、区间包含、
  非法输入与对比请求校验；
- 全量 SQLite：663 passed, 17 skipped。

## 边界

- `VersionRange.contains()` 对缺失 / 非法版本返回 False，不猜测默认版本；
  “未标注版本是否等于最新版”是业务决策，需人工确认；
- 检索执行器尚未把 `VersionRange` 接到 SQL 过滤；当前 Metadata Filter
  只支持精确版本匹配，区间过滤需要扩展 `app/retrieval/filters.py`；
- 跨版本 diff 只定义了请求契约，“内容差异如何呈现给用户”留给
  Phase 25 / 26 的 Agent 工具层。
