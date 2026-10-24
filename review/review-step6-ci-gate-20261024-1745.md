# Review：步骤 6 —— CI PostgreSQL 门禁

- 日期：2026-10-24 17:45（Asia/Shanghai）
- 分支：`phase-12-evaluation-dataset`
- 范围：GitHub Actions 增加 PostgreSQL + pgvector 独立 job

## 变更内容

- `.github/workflows/ci.yml`：新增 `postgres` job：
  - service 容器 `pgvector/pgvector:pg17`（与本地 docker-compose 同镜像），health check 通过后才跑步骤；
  - 空库上 `alembic upgrade head` → `alembic check` → 全量 pytest（`TEST_DATABASE_URL` 指向 service 库）；
  - 模型服务地址指向无效端口，Embedding / LLM 相关测试按既有 skip 逻辑跳过，CI 不依赖本机 OMLX。
- 原 `test` job（SQLite + ruff）保持不变。

## 验证

- YAML 结构本地解析通过（两个 job、service 配置完整）。
- 等价演练：本地以同一 `TEST_DATABASE_URL` 方式跑全量 pytest，731 passed / 2 skipped（此前步骤 4 已验证），`alembic upgrade head` + `alembic check` 通过。
- SQLite 全量：722 passed；`ruff`、`git diff --check` 通过。

## Review 发现与处置

1. 【已修】编排过程中多次字符串替换把凭据字段名改坏（`POSTGRES_PASSWORD` 被拼接成非法变量名、service 行缩进被破坏）；最终放弃增量替换，从 `.env` 读取真实凭据后一次性重建追加块，并用 dotenv 回读断言 user / password / URL / health-cmd 四处一致。
2. 【确认设计】CI 与本地共用同一镜像和迁移路径，"空库 → head → 全量测试"等价于新环境建库验收；`alembic check` 在 upgrade 之后执行，防止 ORM 与迁移漂移合入主分支。
3. 【确认边界】job 名与凭据沿用本地 compose 的默认值（非 secret），无泄露风险；未来若换私有库，需改用 repository secrets。
4. 【遗留】job 未加并发取消与缓存（uv 缓存可加速）；当前规模下总时长可接受。

## 结论

PostgreSQL 门禁进入 CI，手工验证的缺口（台账 P2 项）闭环。可以进入下一项。
