#!/usr/bin/env bash
# 一键初始化数据库：启动容器 → 等待 healthy → 执行 Alembic 迁移。
#
# 用法：./scripts/db_init.sh
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> 启动 PostgreSQL 容器"
docker compose up -d

echo "==> 等待数据库就绪"
for _ in $(seq 1 30); do
  if docker compose exec -T postgres pg_isready -U "${POSTGRES_USER:-postgres}" \
      -d "${POSTGRES_DB:-ai_test_knowledge}" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "==> 执行迁移（建表 + 建扩展）"
uv run alembic upgrade head

echo "==> 完成。可用以下命令验证："
echo "    docker compose exec postgres psql -U postgres -d ${POSTGRES_DB:-ai_test_knowledge} -c '\\dt'"
