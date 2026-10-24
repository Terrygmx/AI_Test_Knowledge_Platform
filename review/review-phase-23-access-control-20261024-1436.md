# Phase 23 Review：检索层权限

- 日期：2026-10-24 14:36（Asia/Shanghai）
- 分支：`phase-12-evaluation-dataset`
- 结论：**访问控制契约与候选过滤已实现；数据库行级过滤待权限字段建模后接入**

## 已实现

- 新增 `app/retrieval/access_control.py`：
  - MVP 维度为 `team` / `project` / `role`，角色分 viewer / editor / admin；
  - `AccessRule` 中 `None` 表示该维度不限；显式空列表被拒绝，
    避免“看起来配置了但实际全放行”；
  - `filter_accessible()` 在候选进入排序前过滤，符合
    “Retrieval 层过滤，而不是召回后隐藏”的计划原则；
  - 无规则（`rule_of` 返回 None）的资源默认公开，是显式语义而不是遗漏。

## 测试

- 新增 `tests/test_access_control.py`：多维度组合、角色层级、
  空列表拒绝、过滤前移与默认公开；
- 全量 SQLite：668 passed, 17 skipped。

## 边界

- 数据模型还没有权限字段；`KnowledgeChunk.meta` 可临时承载
  team / project 标注，但正式方案需要 ORM 字段 + 迁移 + SQL 谓词；
- 角色只有三档，不做资源级 ACL；细粒度授权留到真实权限系统接入时；
- `filter_accessible()` 是 Python 层实现，只能用于已召回候选，
  SQL pre-filter 版本必须在权限字段建模后补齐，否则大库下先召回再过滤
  仍会浪费候选深度。
