"""Alembic 运行环境。

Schema 的**单一事实来源是 ORM（``app.models``）**，不再是 ``sql/init/*.sql``：
以前 DDL 文件与 ORM 各写一份，必然漂移；现在迁移从模型生成 / 与模型对齐，
测试里还有漂移检查（``tests/test_migrations.py``）。

连接参数同样只来自 ``app.core.config``（即 ``.env``），这里不出现硬编码 DSN。
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# 导入 models 包：把所有 ORM 表注册进 Base.metadata
from app import models  # noqa: F401
from app.core.config import settings
from app.core.db import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 连接参数默认来自 .env；若调用方（测试 / CI）已显式指定 URL，则尊重调用方，
# 这样同一套迁移可以在临时 SQLite 库上验证可执行性。
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：只打印 SQL，不连接数据库（``alembic upgrade head --sql``）。"""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
