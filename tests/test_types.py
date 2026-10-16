"""可移植列类型测试（app/models/types.py）。

这些类型让同一套 ORM 既能跑在 PostgreSQL（JSONB / TEXT[]）上，
也能跑在 SQLite（JSON 文本）上，从而在没有 Docker 的环境下完成 TDD。
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect

from app.models import Defect, Document, TestCase
from app.models.types import JSONMap, TextArray


class TestTextArray:
    def test_renders_native_array_on_postgresql(self) -> None:
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.schema import CreateTable

        ddl = str(CreateTable(TestCase.__table__).compile(dialect=postgresql.dialect()))
        assert "tags TEXT[]" in ddl

    def test_renders_text_on_sqlite(self) -> None:
        from sqlalchemy.dialects import sqlite as sqlite_dialect
        from sqlalchemy.schema import CreateTable

        ddl = str(CreateTable(TestCase.__table__).compile(dialect=sqlite_dialect.dialect()))
        assert "tags TEXT NOT NULL" in ddl
        assert "TEXT[]" not in ddl

    def test_roundtrip_unicode_tags(self, db_session) -> None:
        case = TestCase(case_id="TAG-001", title="标签往返", tags=["冒烟", "登录", "smoke"])
        db_session.add(case)
        db_session.commit()
        db_session.refresh(case)
        assert case.tags == ["冒烟", "登录", "smoke"]

    def test_bind_and_result_processing_on_sqlite(self) -> None:
        """SQLite 侧序列化为 JSON 文本，读出来还原成 list。"""
        from sqlalchemy.dialects import sqlite

        dialect = sqlite.dialect()
        assert TextArray().process_bind_param(["a", "冒烟"], dialect) == '["a", "冒烟"]'
        assert TextArray().process_result_value('["a", "冒烟"]', dialect) == ["a", "冒烟"]

    def test_bind_and_result_processing_on_postgresql(self) -> None:
        """PG 侧保持原生 list，交给 text[] 处理，不能被 JSON 化。"""
        from sqlalchemy.dialects import postgresql

        dialect = postgresql.dialect()
        assert TextArray().process_bind_param(["a", "b"], dialect) == ["a", "b"]
        assert TextArray().process_result_value(["a", "b"], dialect) == ["a", "b"]

    def test_none_passthrough(self) -> None:
        from sqlalchemy.dialects import sqlite

        assert TextArray().process_bind_param(None, sqlite.dialect()) is None
        assert TextArray().process_result_value(None, sqlite.dialect()) is None


class TestJSONMap:
    def test_renders_jsonb_on_postgresql(self) -> None:
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.schema import CreateTable

        ddl = str(CreateTable(Document.__table__).compile(dialect=postgresql.dialect()))
        assert "metadata JSONB" in ddl

    def test_metadata_roundtrip(self, db_session) -> None:
        doc = Document(doc_id="DOC-100", title="需求文档", meta={"module": "login", "v": 2})
        db_session.add(doc)
        db_session.commit()
        db_session.refresh(doc)
        assert doc.meta == {"module": "login", "v": 2}

    def test_defect_related_cases_roundtrip(self, db_session) -> None:
        defect = Defect(bug_id="BUG-2000", title="支付超时", related_cases=["PAY-001", "PAY-002"])
        db_session.add(defect)
        db_session.commit()
        db_session.refresh(defect)
        assert defect.related_cases == ["PAY-001", "PAY-002"]


class TestTypeObjects:
    @pytest.mark.parametrize("tp", [TextArray(), JSONMap()])
    def test_cache_ok_enabled(self, tp) -> None:
        assert tp.cache_ok is True

    def test_inspector_can_read_created_schema(self, engine) -> None:
        inspector = inspect(engine)
        assert "test_cases" in inspector.get_table_names()
