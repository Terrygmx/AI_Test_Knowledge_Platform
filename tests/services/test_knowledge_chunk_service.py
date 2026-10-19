"""KnowledgeChunk 服务与实体索引测试。"""

from __future__ import annotations

import pytest

from app.core.exceptions import DomainError
from app.models import Defect, Document, Requirement, TestCase
from app.retrieval.embedding import FakeEmbeddingClient
from app.schemas.knowledge_chunk import KnowledgeChunkCreate
from app.services.index_service import IndexService, case_to_chunk
from app.services.knowledge_chunk_service import KnowledgeChunkService


class TestKnowledgeChunkCreate:
    def test_create_without_embedding_computes_hash(self, db_session) -> None:
        chunk = KnowledgeChunkService.create(
            db_session,
            data=KnowledgeChunkCreate(
                source_type="testcase",
                source_id="LOGIN-001",
                content="登录成功",
                metadata={"module": "login"},
            ),
        )
        assert chunk.id is not None
        assert chunk.embedding is None
        assert len(chunk.content_hash) == 64
        assert chunk.meta == {"module": "login"}

    def test_create_with_embedding(self, db_session) -> None:
        client = FakeEmbeddingClient(dimension=1024)
        chunk = KnowledgeChunkService.create(
            db_session,
            data=KnowledgeChunkCreate(
                source_type="defect",
                source_id="BUG-1",
                content="支付超时",
            ),
            embedding_client=client,
        )
        assert chunk.embedding == client.embed_query("支付超时")
        assert len(chunk.embedding) == 1024

    def test_embedding_dimension_mismatch_is_rejected(self, db_session) -> None:
        with pytest.raises(DomainError, match="维度"):
            KnowledgeChunkService.create(
                db_session,
                data=KnowledgeChunkCreate(
                    source_type="testcase",
                    source_id="LOGIN-002",
                    content="x",
                ),
                embedding_client=FakeEmbeddingClient(dimension=8),
            )

    def test_metadata_kwarg_is_rejected(self) -> None:
        from app.models import KnowledgeChunk

        with pytest.raises(TypeError, match="meta="):
            KnowledgeChunk(
                source_type="testcase",
                source_id="A-1",
                content="x",
                content_hash="a" * 64,
                metadata={},
            )

    def test_duplicate_source_chunk_is_rejected_as_duplicate(self, db_session) -> None:
        from app.core.exceptions import DuplicateError

        data = KnowledgeChunkCreate(
            source_type="testcase",
            source_id="LOGIN-003",
            chunk_index=0,
            content="x",
        )
        KnowledgeChunkService.create(db_session, data=data)
        with pytest.raises(DuplicateError) as exc:
            KnowledgeChunkService.create(db_session, data=data)
        assert exc.value.code == "duplicate"


class TestEntityMapping:
    def test_testcase_mapping_contains_key_fields(self) -> None:
        case = TestCase(
            case_id="LOGIN-001",
            title="短信登录冒烟",
            precondition="账号已注册",
            steps="输入手机号",
            expected_result="登录成功",
            tags=["smoke"],
            module="login",
            priority="P0",
        )
        chunk = case_to_chunk(case)
        assert chunk.source_type == "testcase"
        assert chunk.source_id == "LOGIN-001"
        assert "短信登录冒烟" in chunk.content
        assert chunk.metadata["priority"] == "P0"

    def test_defect_and_requirement_mapping(self) -> None:
        from app.services.index_service import defect_to_chunk, requirement_to_chunk

        defect_chunk = defect_to_chunk(
            Defect(bug_id="BUG-1", title="支付失败", root_cause="超时")
        )
        assert defect_chunk.source_type == "defect"
        assert "根因：超时" in defect_chunk.content

        req_chunk = requirement_to_chunk(
            Requirement(req_id="REQ-1", title="支付改版", content="新增退款")
        )
        assert req_chunk.source_type == "requirement"
        assert "新增退款" in req_chunk.content

    def test_document_mapping_uses_supplied_content(self) -> None:
        from app.services.index_service import document_to_chunk

        chunk = document_to_chunk(
            Document(doc_id="DOC-1", title="规范"),
            content="第一段内容",
            chunk_index=2,
        )
        assert chunk.document_id == "DOC-1"
        assert chunk.chunk_index == 2
        assert chunk.content == "第一段内容"

    def test_index_entities_creates_chunks(self, db_session) -> None:
        case = TestCase(case_id="LOGIN-100", title="登录")
        defect = Defect(bug_id="BUG-100", title="登录失败")
        db_session.add_all([case, defect])
        db_session.commit()

        chunks = IndexService.index_entities(
            db_session,
            embedding_client=FakeEmbeddingClient(dimension=1024),
            testcases=[case],
            defects=[defect],
        )
        assert len(chunks) == 2
        assert {chunk.source_type for chunk in chunks} == {"testcase", "defect"}
        assert all(chunk.embedding is not None for chunk in chunks)
