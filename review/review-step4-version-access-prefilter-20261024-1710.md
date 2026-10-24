# Review：步骤 4 —— 版本 / 权限 SQL pre-filter

- 日期：2026-10-24 17:10（Asia/Shanghai）
- 分支：`phase-12-evaluation-dataset`
- 范围：Phase 22 版本区间、Phase 23 检索层权限下沉到 SQL

## 变更内容

- `app/retrieval/versioning.py`：新增 `version_key()`——零填充可排序键（`v2.10` → `0002.0010.0000`），非法 / 缺失版本返回 None。
- `app/models/knowledge_chunk.py`：新增 `version_key` 列 + 索引；`alembic/versions/0004` 加列并对存量数据回填，非法版本保持 NULL。
- `app/retrieval/access_filters.py`：版本区间谓词（作用于 `version_key` 列）+ 权限谓词（PG 用 JSONB `@>` 与 CASE 角色等级；维度未设置为"不限"；畸形 access fail-closed）。
- `app/services/knowledge_chunk_service.py`：`search` / `lexical_search` / `hybrid_search` 接受 `version_range` 与 `access_context`，全部在 LIMIT 前过滤；create / create_many 写入 `version_key`；Python 侧权限复核兜底（SQLite SQL 层只排除畸形块）。
- `tests/test_version_access_filters.py`：11 个测试覆盖排序键、数值区间、缺失版本排除、缺省可见、team / role / 畸形 access 拒绝、无 context 不过滤。

## 验证

- SQLite：716 passed, 17 skipped；PostgreSQL：731 passed, 2 skipped（双库全量）。
- `alembic upgrade head`（0003 → 0004）与 `alembic check` 通过；`ruff`、`git diff --check` 通过。

## Review 发现与处置

1. 【已修，P0】PG 权限谓词最初把 teams / projects 判断绑在"access 块存在"上：规则只设置 `min_role`（teams / projects 缺失）时，`->'teams'` 为 NULL 导致 `@>` 不成立，合法用户被拒。修正为"维度缺失视为不限"（`teams_absent | teams_ok`），与 `AccessRule` 的 None 语义对齐。回归测试 `test_editor_role_passes_min_role_editor` 在 PG 上验证。
2. 【已修，P1】`sync` 之外复用同一教训：`search` 是 PostgreSQL-only 入口，SQLite 全量测试误用它导致假失败；测试改用 `lexical_search` 验证过滤语义，入口边界与既有约定一致。
3. 【已修】`tests/test_migrations.py` 的静态解析器不识别 `add_column`，且初版误写 `len(names) >= 2`（`add_column` 只有 1 个字符串参数）；已修正，迁移漂移检查重新覆盖 0004。
4. 【确认语义】`version_key` 只在写入时计算，存量行由迁移回填；若调用方绕过 service 直写 `version` 会出现 key 漂移，`alembic check` 无法捕获——这是已知边界，正式环境应禁止旁路写入。
5. 【确认边界】SQLite 上完整权限判定由 Python 复核兜底（fail-closed），PG 上 SQL 与 Python 双层判定结果一致；document Chunk 同样适用（meta 含 access 时生效）。

## 结论

版本区间与权限过滤已下沉到 SQL pre-filter 并通过双库全量验证；1 个 P0（PG 权限误拒）在 review 中发现并修复。可以进入下一项。
