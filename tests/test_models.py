"""ORM 实体形状测试。

只验证「表结构是否与设计一致」：表名、列集合、可空性、唯一约束、索引名。
这些断言不依赖具体数据库，SQLite 下也能跑（建表由 conftest 的 engine 夹具完成）。
"""

from __future__ import annotations

import pytest
from sqlalchemy import BigInteger, DateTime, Integer, String, Text

from app.core.db import Base
from app.models import Defect, Document, KnowledgeChunk, Requirement, TestCase


def _columns(table_name: str) -> set[str]:
    return {col.name for col in Base.metadata.tables[table_name].columns}


def _indexes(table_name: str) -> set[str]:
    return {idx.name for idx in Base.metadata.tables[table_name].indexes}


TEST_CASE_COLUMNS = {
    "id",
    "case_id",
    "title",
    "module",
    "sub_module",
    "version",
    "priority",
    "case_type",
    "precondition",
    "steps",
    "expected_result",
    "tags",
    "source",
    "created_at",
    "updated_at",
}


class TestTestCaseModel:
    def test_table_name_and_columns(self) -> None:
        assert TestCase.__tablename__ == "test_cases"
        assert _columns("test_cases") == TEST_CASE_COLUMNS

    def test_case_id_is_unique_and_required(self) -> None:
        col = Base.metadata.tables["test_cases"].c.case_id
        assert col.unique is True
        assert col.nullable is False

    def test_primary_key_is_bigint(self) -> None:
        pk = Base.metadata.tables["test_cases"].primary_key
        assert [c.name for c in pk.columns] == ["id"]
        assert isinstance(pk.columns["id"].type, BigInteger)

    def test_long_text_columns(self) -> None:
        table = Base.metadata.tables["test_cases"]
        for name in ("title", "precondition", "steps", "expected_result"):
            assert isinstance(table.c[name].type, Text), name

    def test_indexes_match_ddl(self) -> None:
        assert _indexes("test_cases") == {"idx_test_cases_filter", "idx_test_cases_tags"}

    def test_tags_default_to_empty_list(self, db_session) -> None:
        case = TestCase(case_id="LOGIN-001", title="短信登录冒烟")
        db_session.add(case)
        db_session.commit()
        db_session.refresh(case)
        assert case.tags == []

    def test_timestamps_are_populated_by_database(self, db_session) -> None:
        case = TestCase(case_id="LOGIN-002", title="账号密码登录")
        db_session.add(case)
        db_session.commit()
        db_session.refresh(case)
        assert case.created_at is not None
        assert case.updated_at is not None

    def test_duplicate_case_id_is_rejected(self, db_session) -> None:
        from sqlalchemy.exc import IntegrityError

        db_session.add(TestCase(case_id="LOGIN-003", title="a"))
        db_session.commit()
        db_session.add(TestCase(case_id="LOGIN-003", title="b"))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()


DEFECT_COLUMNS = {
    "id",
    "bug_id",
    "title",
    "module",
    "version",
    "severity",
    "priority",
    "status",
    "reproduce_steps",
    "root_cause",
    "resolution",
    "related_cases",
    "source",
    "created_at",
    "updated_at",
}


class TestDefectModel:
    def test_table_name_and_columns(self) -> None:
        assert Defect.__tablename__ == "defects"
        assert _columns("defects") == DEFECT_COLUMNS

    def test_bug_id_is_unique_and_required(self) -> None:
        col = Base.metadata.tables["defects"].c.bug_id
        assert col.unique is True
        assert col.nullable is False

    def test_indexes_match_ddl(self) -> None:
        assert _indexes("defects") == {"idx_defects_filter"}

    def test_related_cases_default_to_empty_list(self, db_session) -> None:
        defect = Defect(bug_id="BUG-1021", title="支付失败")
        db_session.add(defect)
        db_session.commit()
        db_session.refresh(defect)
        assert defect.related_cases == []


class TestRequirementModel:
    def test_table_name_and_columns(self) -> None:
        assert Requirement.__tablename__ == "requirements"
        assert _columns("requirements") == {
            "id",
            "req_id",
            "title",
            "module",
            "version",
            "priority",
            "status",
            "content",
            "source",
            "created_at",
            "updated_at",
        }

    def test_indexes_match_ddl(self) -> None:
        assert _indexes("requirements") == {"idx_requirements_filter"}


class TestDocumentModel:
    def test_table_name_and_columns(self) -> None:
        assert Document.__tablename__ == "documents"
        assert _columns("documents") == {
            "id",
            "doc_id",
            "title",
            "doc_type",
            "path",
            "content_hash",
            "version",
            "metadata",
            "created_at",
            "updated_at",
        }

    def test_indexes_match_ddl(self) -> None:
        assert _indexes("documents") == {"idx_documents_hash", "idx_documents_version"}

    def test_metadata_defaults_to_empty_dict(self, db_session) -> None:
        doc = Document(doc_id="DOC-001", title="登录模块需求")
        db_session.add(doc)
        db_session.commit()
        db_session.refresh(doc)
        assert doc.meta == {}

    def test_metadata_kwarg_is_rejected(self) -> None:
        """Document(metadata=...) 会覆盖类级 MetaData，必须 fail fast。"""
        with pytest.raises(TypeError, match="meta="):
            Document(doc_id="DOC-002", title="x", metadata={"a": 1})


class TestColumnTypes:
    def test_bounded_columns_use_string(self) -> None:
        table = Base.metadata.tables["test_cases"]
        for name in (
            "case_id",
            "module",
            "sub_module",
            "version",
            "priority",
            "case_type",
            "source",
        ):
            assert isinstance(table.c[name].type, String), name

    def test_created_at_is_timezone_aware_column(self) -> None:
        table = Base.metadata.tables["test_cases"]
        assert isinstance(table.c.created_at.type, DateTime)
        assert table.c.created_at.type.timezone is True

    def test_id_is_integer(self) -> None:
        assert isinstance(Base.metadata.tables["defects"].c.id.type, Integer)


class TestKnowledgeChunkModel:
    def test_table_name_and_columns(self) -> None:
        assert KnowledgeChunk.__tablename__ == "knowledge_chunks"
        assert _columns("knowledge_chunks") == {
            "id",
            "source_type",
            "source_id",
            "document_id",
            "chunk_index",
            "content",
            "metadata",
            "embedding",
            "content_hash",
            "version",
            "created_at",
            "updated_at",
        }

    def test_indexes_and_unique_constraint(self) -> None:
        assert _indexes("knowledge_chunks") == {
            "idx_knowledge_chunks_content_hash",
            "idx_knowledge_chunks_document",
            "idx_knowledge_chunks_embedding_hnsw",
            "idx_knowledge_chunks_source",
        }
        table = Base.metadata.tables["knowledge_chunks"]
        unique_names = {
            constraint.name
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert "uq_knowledge_chunks_source_chunk" in unique_names

    def test_embedding_is_vector_1024(self) -> None:
        from pgvector.sqlalchemy import Vector

        embedding = Base.metadata.tables["knowledge_chunks"].c.embedding
        assert isinstance(embedding.type, Vector)
        assert embedding.type.dim == 1024
        assert embedding.nullable is True
