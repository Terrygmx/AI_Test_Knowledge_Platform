# Review：步骤 3 —— 增量索引执行器 + CLI

- 日期：2026-10-24 16:45（Asia/Shanghai）
- 分支：`phase-12-evaluation-dataset`
- 范围：Phase 21 计划器的执行层与命令行入口

## 变更内容

- `app/services/incremental_index_service.py`：`sync_incremental_index()` 执行 CREATE / UPDATE / DELETE；`desired_states_from_entities()` 复用 Phase 1 的 `case_to_chunk` / `defect_to_chunk` / `requirement_to_chunk` 组装正文，保证 hash 对比与全量索引的内容口径一致；`existing_entity_hashes()` 读取库内 hash。
- `scripts/incremental_index.py`：`--dry-run` 输出 JSON 计划 + 统计；默认执行同步（真实 Embedding）；`--only` 限定实体类型；document 不在同步范围（由导入 + 补向量流程负责）。
- `tests/test_incremental_index_service.py`：6 个测试覆盖首建并 Embedding、无变化复跑幂等、内容变化重嵌、实体删除清理、计划类型统计、期望状态覆盖。

## 验证

- `uv run pytest -o addopts='' -q tests/test_incremental_index_service.py tests/test_incremental_index.py tests/test_badcase_persistence.py tests/api`：111 passed, 1 skipped。
- `uv run ruff check .`、`git diff --check`、`alembic check` 通过。

## Review 发现与处置

1. 【已修，P1】DELETE 检测的 scope 最初从 desired 推导：当范围内实体被全部删除时 desired 为空集合，`existing_entity_hashes()` 收到空范围，DELETE 永远不会出现——增量同步最核心的清理路径失效。改为调用方显式声明 `source_types` 范围，CLI 传入 `--only` 选中的类型全集。回归测试 `test_deleted_entity_is_cleaned` 验证。
2. 【确认边界】一实体多 Chunk（chunk_index > 0）目前只有 document；实体同步范围只含 testcase / defect / requirement，hash 取该实体任一 Chunk（实际唯一 chunk_index=0）。若未来实体切成多 Chunk，需要升级为逐 Chunk hash 对比，这是既有台账中"Chunk 级对比未实现"缺口，本步骤不扩大范围。
3. 【确认语义】UPDATE 路径按"先删后建"执行并重新 Embedding，与 Phase 5 导入替换语义一致；CREATE 与 UPDATE 都计入 `embedded`，CLI 输出如实展示。
4. 【遗留】dry-run 在真实库上执行时只读不写，不调用 Embedding；执行路径的取消 / 断点续跑未做（当前规模下单事务可接受）。

## 结论

计划 → 执行 → 幂等复跑闭环已通，review 抓出并修复 1 个 P1 的 DELETE 失效缺陷。可以进入下一项。
