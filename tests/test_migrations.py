"""迁移与 ORM 的一致性检查。

两类保障：
1. **漂移检查**：ORM 里有的表 / 列 / 索引 / 唯一约束，迁移里必须也有
   （新增模型忘写迁移时会立刻失败）；
2. **可执行性**：迁移真的能在数据库上跑通 upgrade + downgrade
   （用 SQLite 临时库执行，不依赖 Docker）。

Schema 的单一事实来源是 ORM；``alembic/versions`` 是它的版本化投影。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from sqlalchemy import UniqueConstraint, inspect, text
from sqlalchemy.engine import Engine

from app import models  # noqa: F401
from app.core.db import Base

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"


@dataclass
class MigrationTable:
    columns: set[str] = field(default_factory=set)
    unique: set[tuple[str, ...]] = field(default_factory=set)
    indexes: set[str] = field(default_factory=set)


def _string_args(call: ast.Call) -> list[str]:
    return [
        arg.value
        for arg in call.args
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
    ]


def _parse_migrations() -> dict[str, MigrationTable]:
    """用 ast 静态解析迁移脚本，避免 import 副作用。"""
    tables: dict[str, MigrationTable] = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        if path.name.startswith("__"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue

            if node.func.attr == "create_table":
                names = _string_args(node)
                if not names:
                    continue
                table = tables.setdefault(names[0], MigrationTable())
                for sub in ast.walk(node):
                    if not isinstance(sub, ast.Call) or not isinstance(sub.func, ast.Attribute):
                        continue
                    if sub.func.attr == "Column":
                        cols = _string_args(sub)
                        if cols:
                            table.columns.add(cols[0])
                    elif sub.func.attr == "UniqueConstraint":
                        table.unique.add(tuple(sorted(_string_args(sub))))

            elif node.func.attr == "create_index":
                names = _string_args(node)
                if len(names) >= 2:
                    tables.setdefault(names[1], MigrationTable()).indexes.add(names[0])
            elif node.func.attr == "execute":
                names = _string_args(node)
                if not names:
                    continue
                sql = names[0]
                if "USING hnsw" in sql and " ON knowledge_chunks " in sql:
                    tables.setdefault("knowledge_chunks", MigrationTable()).indexes.add(
                        "idx_knowledge_chunks_embedding_hnsw"
                    )
    return tables


def _orm_tables() -> dict[str, MigrationTable]:
    tables: dict[str, MigrationTable] = {}
    for name, table in Base.metadata.tables.items():
        entry = MigrationTable()
        entry.columns = {col.name for col in table.columns}
        entry.unique = {
            tuple(sorted(col.name for col in uc.columns))
            for uc in table.constraints
            if isinstance(uc, UniqueConstraint)
        }
        entry.indexes = {idx.name for idx in table.indexes if idx.name}
        tables[name] = entry
    return tables


class TestMigrationDrift:
    def test_versions_directory_exists(self) -> None:
        assert VERSIONS_DIR.is_dir(), VERSIONS_DIR
        assert list(VERSIONS_DIR.glob("*.py")), "迁移目录为空"

    def test_tables_match_orm(self) -> None:
        assert set(_parse_migrations()) == set(_orm_tables())

    @pytest.mark.parametrize("table_name", sorted(Base.metadata.tables))
    def test_columns_match_orm(self, table_name: str) -> None:
        migration = _parse_migrations()[table_name]
        orm = _orm_tables()[table_name]
        assert migration.columns == orm.columns, (
            f"{table_name} 列不一致：迁移独有 {migration.columns - orm.columns}，"
            f"ORM 独有 {orm.columns - migration.columns}"
        )

    @pytest.mark.parametrize("table_name", sorted(Base.metadata.tables))
    def test_indexes_match_orm(self, table_name: str) -> None:
        migration = _parse_migrations()[table_name]
        orm = _orm_tables()[table_name]
        assert migration.indexes == orm.indexes

    @pytest.mark.parametrize("table_name", sorted(Base.metadata.tables))
    def test_unique_constraints_match_orm(self, table_name: str) -> None:
        migration = _parse_migrations()[table_name]
        orm = _orm_tables()[table_name]
        assert migration.unique == orm.unique

    def test_hnsw_index_has_stable_marker(self) -> None:
        """PG 专属索引用注释标记，避免解析器依赖 SQL 字符串细节。"""
        migration = (
            Path(__file__).resolve().parents[1]
            / "alembic"
            / "versions"
            / "0002_knowledge_chunks.py"
        ).read_text(encoding="utf-8")
        assert "hnsw: idx_knowledge_chunks_embedding_hnsw" in migration


class TestMigrationExecutable:
    def test_upgrade_and_downgrade_roundtrip(self, tmp_path) -> None:
        """迁移必须真的能执行（用临时 SQLite 库，无需 Docker）。"""
        from alembic.config import Config

        from alembic import command

        db_file = tmp_path / "migration_check.db"
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", f"sqlite:///{db_file}")

        command.upgrade(config, "head")

        from sqlalchemy import create_engine

        engine = create_engine(f"sqlite:///{db_file}")
        try:
            created = set(inspect(engine).get_table_names())
            # alembic_version 是 Alembic 自己的版本表
            assert created == set(Base.metadata.tables) | {"alembic_version"}, created
        finally:
            engine.dispose()

        command.downgrade(config, "base")
        engine = create_engine(f"sqlite:///{db_file}")
        try:
            remaining = set(inspect(engine).get_table_names())
            assert remaining == {"alembic_version"}, remaining
        finally:
            engine.dispose()

    def test_postgresql_extensions_present(self, engine: Engine) -> None:
        """真实 PostgreSQL 上，迁移必须安装向量与三元组检索扩展。"""
        if engine.dialect.name != "postgresql":
            pytest.skip("PostgreSQL-only check")

        from alembic.config import Config

        from alembic import command

        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", engine.url.render_as_string(hide_password=False))

        # 测试夹具已 create_all；清空 ORM 表与 Alembic 版本表，从 base 验证迁移。
        Base.metadata.drop_all(engine)
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))

        command.upgrade(config, "head")

        try:
            with engine.connect() as conn:
                installed = set(
                    conn.execute(
                        text(
                            "SELECT extname FROM pg_extension "
                            "WHERE extname IN ('vector', 'pg_trgm')"
                        )
                    ).scalars()
                )

                assert installed == {"vector", "pg_trgm"}

                hnsw_indexes = set(
                    conn.execute(
                        text(
                            "SELECT indexname FROM pg_indexes "
                            "WHERE tablename = 'knowledge_chunks' "
                            "AND indexname = 'idx_knowledge_chunks_embedding_hnsw'"
                        )
                    ).scalars()
                )
                assert hnsw_indexes == {"idx_knowledge_chunks_embedding_hnsw"}
        finally:
            command.downgrade(config, "base")
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
