"""PostgreSQL + pgvector 真实向量检索测试。

默认 SQLite 路径跳过；设置 ``TEST_DATABASE_URL`` 后执行。
"""

from __future__ import annotations

import pytest

from app.core.exceptions import DomainError
from app.generation.rag import INSUFFICIENT_EVIDENCE, RAGService
from app.retrieval.embedding import FakeEmbeddingClient
from app.schemas.knowledge_chunk import KnowledgeChunkCreate
from app.services.knowledge_chunk_service import KnowledgeChunkService


class TestVectorSearch:
    def test_cosine_search_returns_scored_results(self, engine, db_session) -> None:
        if engine.dialect.name != "postgresql":
            pytest.skip("PostgreSQL + pgvector only")

        client = FakeEmbeddingClient(dimension=1024)
        items = [
            KnowledgeChunkCreate(
                source_type="testcase",
                source_id="LOGIN-001",
                content="登录成功",
            ),
            KnowledgeChunkCreate(
                source_type="defect",
                source_id="BUG-001",
                content="支付失败",
            ),
        ]
        KnowledgeChunkService.create_many(
            db_session,
            items=items,
            embedding_client=client,
        )

        results = KnowledgeChunkService.search(
            db_session,
            query_embedding=client.embed_query("登录成功"),
            limit=2,
        )
        assert {chunk.source_id for chunk, _ in results} == {"LOGIN-001", "BUG-001"}
        # 排序由 pgvector cosine distance 完成；Fake Embedding 不代表语义召回质量。
        assert all(0.0 <= distance <= 2.0 for _, distance in results)
        assert results == sorted(results, key=lambda item: item[1])

    def test_source_type_filter(self, engine, db_session) -> None:
        if engine.dialect.name != "postgresql":
            pytest.skip("PostgreSQL + pgvector only")

        client = FakeEmbeddingClient(dimension=1024)
        KnowledgeChunkService.create_many(
            db_session,
            items=[
                KnowledgeChunkCreate(
                    source_type="testcase",
                    source_id="A-1",
                    content="登录",
                ),
                KnowledgeChunkCreate(
                    source_type="defect",
                    source_id="B-1",
                    content="支付",
                ),
            ],
            embedding_client=client,
        )
        results = KnowledgeChunkService.search(
            db_session,
            query_embedding=client.embed_query("登录"),
            source_types=["defect"],
        )
        assert [chunk.source_id for chunk, _ in results] == ["B-1"]

    def test_sqlite_rejects_vector_search(self, engine, db_session) -> None:
        if engine.dialect.name == "postgresql":
            pytest.skip("SQLite-only check")
        with pytest.raises(DomainError, match="PostgreSQL"):
            KnowledgeChunkService.search(
                db_session,
                query_embedding=[0.0] * 1024,
            )

    def test_rag_pipeline_returns_citation(self, engine, db_session) -> None:
        if engine.dialect.name != "postgresql":
            pytest.skip("PostgreSQL + pgvector only")

        class EchoLLM:
            def generate(self, prompt: str) -> str:
                assert "LOGIN-001" in prompt
                return "登录失败时应检查验证码流程。[1]"

        client = FakeEmbeddingClient(dimension=1024)
        KnowledgeChunkService.create(
            db_session,
            data=KnowledgeChunkCreate(
                source_type="testcase",
                source_id="LOGIN-001",
                content="用例编号：LOGIN-001\n标题：登录失败\n预期结果：提示错误",
            ),
            embedding_client=client,
        )

        result = RAGService.answer(
            db_session,
            question="登录失败怎么测试？",
            embedding_client=client,
            llm=EchoLLM(),
        )
        assert result.grounded is True
        assert result.answer.endswith("[1]")
        assert result.citations[0].source_id == "LOGIN-001"

    def test_rag_without_evidence_does_not_build_llm(self, engine, db_session) -> None:
        if engine.dialect.name != "postgresql":
            pytest.skip("PostgreSQL + pgvector only")

        def must_not_run():
            raise AssertionError("无证据时不应构造 LLM")

        result = RAGService.answer(
            db_session,
            question="没有知识的问题",
            embedding_client=FakeEmbeddingClient(dimension=1024),
            llm_factory=must_not_run,
        )
        assert result.answer == INSUFFICIENT_EVIDENCE
        assert result.grounded is False
