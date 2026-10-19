"""Phase 3 最小 RAG API 测试。"""

from __future__ import annotations

from app.generation.llm import LLMError
from app.retrieval.embedding import FakeEmbeddingClient
from app.schemas.knowledge_chunk import KnowledgeChunkCreate
from app.services.knowledge_chunk_service import KnowledgeChunkService

API = "/api/v1/rag/answer"


class TestRAGAPI:
    def test_rejects_blank_question(self, client) -> None:
        response = client.post(API, json={"question": "   "})
        assert response.status_code == 422

    def test_vector_search_unavailable_fails_explicitly(
        self, app, client, engine, db_session, monkeypatch
    ) -> None:
        """不支持 pgvector 时必须明确失败，不能伪造检索结果。"""
        from app.api import rag

        monkeypatch.setattr(rag, "get_embedding_client", lambda: FakeEmbeddingClient(dimension=1024))

        def missing_llm():
            raise LLMError("LLM 不可用")

        monkeypatch.setattr(rag, "get_llm_client", missing_llm)
        response = client.post(API, json={"question": "登录失败怎么排查？"})
        if engine.dialect.name == "postgresql":
            assert response.status_code == 200
            body = response.json()
            assert body["citation_valid"] is False
            assert body["evidence_sufficient"] is False
        else:
            assert response.status_code == 400
            assert response.json()["error"]["code"] == "domain_error"

    def test_rag_answer_returns_grounded_answer(
        self, client, engine, db_session, monkeypatch
    ) -> None:
        if engine.dialect.name != "postgresql":
            import pytest

            pytest.skip("PostgreSQL + pgvector only")

        from app.api import rag

        embedding = FakeEmbeddingClient(dimension=1024)
        KnowledgeChunkService.create(
            db_session,
            data=KnowledgeChunkCreate(
                source_type="testcase",
                source_id="LOGIN-API-1",
                content="用例编号：LOGIN-API-1\n标题：登录失败\n预期结果：提示错误",
            ),
            embedding_client=embedding,
        )

        class FakeLLM:
            def generate(self, prompt: str) -> str:
                return "检查登录流程。[1]"

        monkeypatch.setattr(rag, "get_embedding_client", lambda: embedding)
        monkeypatch.setattr(rag, "get_llm_client", lambda: FakeLLM())

        response = client.post(API, json={"question": "登录失败怎么排查？", "top_k": 1})
        assert response.status_code == 200
        body = response.json()
        assert body["citation_valid"] is True
        assert body["evidence_sufficient"] is None
        assert body["citations"][0]["source_id"] == "LOGIN-API-1"
        assert body["citations"][0]["rank"] == 1
        assert "distance" in body["citations"][0]
