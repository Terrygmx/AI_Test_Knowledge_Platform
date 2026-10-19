"""Phase 3 最小 RAG API 测试。"""

from __future__ import annotations

import time
from threading import Event

import pytest

from app.generation.llm import LLMError
from app.retrieval.embedding import FakeEmbeddingClient
from app.schemas.knowledge_chunk import KnowledgeChunkCreate
from app.services.knowledge_chunk_service import KnowledgeChunkService

API = "/api/v1/rag/answer"


class TestRAGTimeoutBoundary:
    def test_timeout_returns_before_worker_finishes(self, client, monkeypatch) -> None:
        """超时必须真正释放请求：不能在 with 块里等工作线程跑完。"""
        from app.api import rag

        finished = Event()

        def slow_answer(*args, **kwargs):
            time.sleep(2)
            finished.set()

        monkeypatch.setattr(rag.RAGService, "answer", slow_answer)
        monkeypatch.setattr(rag.settings, "rag_request_timeout", 0.01)

        started = time.monotonic()
        response = client.post(API, json={"question": "登录失败怎么排查？"})
        elapsed = time.monotonic() - started

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "llm_error"
        assert elapsed < 1.0, f"超时应在 1s 内返回，实际 {elapsed:.2f}s"
        assert not finished.is_set(), "请求返回时工作线程不应已被等待"
        assert finished.wait(5), "工作线程仍会继续跑完（已知取舍）"

    def test_full_concurrency_is_rejected_immediately(self, client, monkeypatch) -> None:
        """并发名额耗尽时不排队，直接返回 503。"""
        from threading import BoundedSemaphore

        from app.api import rag

        class ExhaustedSemaphore(BoundedSemaphore):
            """恒为满载的信号量；release 容错，避免后台任务结束时误抛。"""

            def acquire(self, blocking: bool = True, timeout: float | None = None) -> bool:
                return False

            def release(self, n: int = 1) -> None:  # noqa: ANN401
                return None

        monkeypatch.setattr(rag, "_slots", ExhaustedSemaphore(1))

        response = client.post(API, json={"question": "登录失败怎么排查？"})

        assert response.status_code == 503
        assert "并发" in response.json()["error"]["message"]


class TestSettingsTimeouts:
    def test_llm_timeout_must_not_exceed_request_budget(self) -> None:
        """预算颠倒会让「30 秒超时」成为无效承诺，必须在配置层报错。"""
        from app.core.config import Settings

        with pytest.raises(Exception, match="RAG_REQUEST_TIMEOUT"):
            Settings(_env_file=None, llm_timeout=120, rag_request_timeout=30)

    def test_default_budget_is_consistent(self) -> None:
        from app.core.config import Settings

        config = Settings(_env_file=None)
        assert config.llm_timeout <= config.rag_request_timeout
        assert config.rag_max_concurrency >= 1


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

    def test_rag_timeout_returns_llm_error(self, client, monkeypatch) -> None:
        from app.api import rag

        def slow_answer(*args, **kwargs):
            time.sleep(0.2)

        monkeypatch.setattr(rag.RAGService, "answer", slow_answer)
        monkeypatch.setattr(rag.settings, "rag_request_timeout", 0.01)

        response = client.post(API, json={"question": "登录失败怎么排查？"})

        assert response.status_code == 503
        body = response.json()
        assert body["error"]["code"] == "llm_error"
        assert "超时" in body["error"]["message"]
