# Review：步骤 2 —— Badcase 持久化

- 日期：2026-10-24 16:30（Asia/Shanghai）
- 分支：`phase-12-evaluation-dataset`
- 范围：Phase 19 Badcase 的数据库表、迁移、service 与 API

## 变更内容

- `app/models/badcase.py`：新增 `EvaluationBadcase` ORM（`evaluation_badcases` 表），`regression_query_ids` 用项目既有 `TextArray`，`evaluation_result` 用 `JSONMap`。
- `alembic/versions/0003_evaluation_badcases.py`：迁移 0003（0002 → 0003），含 stage+status / query 两个索引与 `badcase_id` 唯一约束。
- `app/services/badcase_service.py`：契约对象 ↔ 数据库行双向转换；`create`（重复 → 409）、`save`（upsert，用于状态推进回写）、`get` / `list`。
- `app/api/badcases.py`：`POST /badcases`、`GET /badcases`（stage / status 过滤）、`GET /badcases/{id}`、`POST /badcases/{id}/advance`；闭环规则仍由 Phase 19 契约层强制，API 只做 ValueError → 400 的转译。
- `app/main.py`：注册 `badcases` OpenAPI tag；`app/api/__init__.py`：挂载路由。
- `tests/test_badcase_persistence.py`：8 个测试覆盖 CRUD、重复 409、闭环推进、verified 缺评测结果 400、列表过滤、不存在 404。

## 验证

- `uv run pytest -o addopts='' -q tests/test_badcase_persistence.py tests/api tests/test_rag_pipeline.py`：106 passed, 1 skipped。
- PostgreSQL：`alembic upgrade head`（0002 → 0003）成功；`alembic check` 通过（无未迁移的 model 变更）。
- `uv run ruff check .`、`git diff --check` 通过。

## Review 发现与处置

1. 【已修】`advance` 端点最初用裸 `dict | None` 参数接收 body，FastAPI 会把整个 body 包成该 dict，导致 `{"evaluation_result": null}` 变成非 None dict，可绕过"verified 必须带评测结果"校验。改为显式 `BadcaseAdvance` 请求模型后语义正确。
2. 【已修】契约层 `ValueError` 冒泡成 500；现在端点捕获并转成 `DomainError`（400），与项目"路由不自己拼 error dict"的约定一致。
3. 【已修】`regression_query_ids` 初稿误用 `JSONMap`（dict 类型），与项目 `TextArray`（list[str] 跨方言）约定不符；已改用 `TextArray`。
4. 【确认边界】状态顺序与 verified 约束只在契约层（Pydantic validate_assignment + advance()）强制，数据库不做 CHECK 约束；这是刻意保持单一语义来源，避免双份规则漂移。并发推进同一 badcase 的竞态由 `save()` 全字段覆盖 + 最后写入为准，当前单管理员闭环场景可接受。
5. 【遗留】Phase 13/14 评测产出的 badcase 字符串尚未自动映射为 Badcase 记录（`docs/PHASE_PROGRESS.md` 既有缺口），本步骤只提供持久化能力，映射工具属后续工作。

## 结论

Badcase 闭环可落库、可查询、可经 API 推进，迁移已在 PostgreSQL 验证；3 处实现问题已在收口前修复。可以进入下一项。
