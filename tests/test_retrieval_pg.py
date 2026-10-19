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
    def test_bm25_lexical_search_returns_stable_scores_on_postgresql(
        self, engine, db_session
    ) -> None:
        if engine.dialect.name != "postgresql":
            pytest.skip("PostgreSQL integration check")

        for index, (source_type, source_id, content) in enumerate(
            [
                ("defect", "BUG-1234", "P0 BUG-1234 login-api v1.3 登录接口超时"),
                ("testcase", "TC-2001", "P1 登录接口验证码校验"),
                ("document", "DOC-1", "支付超时后查询网关交易状态"),
            ]
        ):
            KnowledgeChunkService.create(
                db_session,
                data=KnowledgeChunkCreate(
                    source_type=source_type,
                    source_id=source_id,
                    chunk_index=index,
                    content=content,
                ),
            )

        results = KnowledgeChunkService.lexical_search(
            db_session,
            query="P0 BUG-1234 login-api v1.3",
            limit=3,
        )

        assert results[0][0].source_id == "BUG-1234"
        assert results[0][1] >= results[-1][1] > 0
        assert "bug-1234" in results[0][2]

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

    def test_cosine_search_has_stable_identity_tie_break(self, engine, db_session) -> None:
        if engine.dialect.name != "postgresql":
            pytest.skip("PostgreSQL + pgvector only")

        client = FakeEmbeddingClient(dimension=1024)
        items = [
            KnowledgeChunkCreate(
                source_type="defect",
                source_id="B-1",
                chunk_index=0,
                content="相同向量",
            ),
            KnowledgeChunkCreate(
                source_type="testcase",
                source_id="A-1",
                chunk_index=0,
                content="相同向量",
            ),
            KnowledgeChunkCreate(
                source_type="testcase",
                source_id="A-1",
                chunk_index=1,
                content="相同向量",
            ),
        ]
        KnowledgeChunkService.create_many(
            db_session,
            items=items,
            embedding_client=client,
        )
        query_embedding = client.embed_query(items[0].content)

        orders = [
            [
                (chunk.source_type, chunk.source_id, chunk.chunk_index)
                for chunk, _ in KnowledgeChunkService.search(
                    db_session,
                    query_embedding=query_embedding,
                    limit=3,
                )
            ]
            for _ in range(3)
        ]

        expected = [
            ("defect", "B-1", 0),
            ("testcase", "A-1", 0),
            ("testcase", "A-1", 1),
        ]
        assert orders == [expected] * 3

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

    def test_metadata_filter_is_applied_before_vector_limit(self, engine, db_session) -> None:
        """目标 Chunk 与查询更远时，预过滤仍应把它放进 Top-K。"""
        if engine.dialect.name != "postgresql":
            pytest.skip("PostgreSQL + pgvector only")

        client = FakeEmbeddingClient(dimension=1024)
        KnowledgeChunkService.create_many(
            db_session,
            items=[
                KnowledgeChunkCreate(
                    source_type="testcase",
                    source_id="DISTRACTOR",
                    content="登录成功",
                    metadata={"module": "payment", "tags": ["smoke"]},
                ),
                KnowledgeChunkCreate(
                    source_type="testcase",
                    source_id="TARGET",
                    content="支付失败",
                    metadata={"module": "login", "tags": ["security"]},
                ),
            ],
            embedding_client=client,
        )

        results = KnowledgeChunkService.search(
            db_session,
            query_embedding=client.embed_query("登录成功"),
            metadata_filters={"module": "login"},
            limit=1,
        )

        assert [chunk.source_id for chunk, _ in results] == ["TARGET"]

    def test_array_metadata_filter_works_on_jsonb(self, engine, db_session) -> None:
        if engine.dialect.name != "postgresql":
            pytest.skip("PostgreSQL + pgvector only")

        client = FakeEmbeddingClient(dimension=1024)
        KnowledgeChunkService.create_many(
            db_session,
            items=[
                KnowledgeChunkCreate(
                    source_type="testcase",
                    source_id="SMOKE",
                    content="登录",
                    metadata={"tags": ["login", "smoke"]},
                ),
                KnowledgeChunkCreate(
                    source_type="testcase",
                    source_id="SECURITY",
                    content="登录",
                    metadata={"tags": ["security"]},
                ),
            ],
            embedding_client=client,
        )

        results = KnowledgeChunkService.search(
            db_session,
            query_embedding=client.embed_query("登录"),
            metadata_filters={"tags": ["smoke"]},
            limit=2,
        )

        assert [chunk.source_id for chunk, _ in results] == ["SMOKE"]

    def test_hybrid_metadata_filter_does_not_leak_from_other_modules(
        self, engine, db_session
    ) -> None:
        if engine.dialect.name != "postgresql":
            pytest.skip("PostgreSQL + pgvector only")

        client = FakeEmbeddingClient(dimension=1024)
        KnowledgeChunkService.create_many(
            db_session,
            items=[
                KnowledgeChunkCreate(
                    source_type="testcase",
                    source_id="LOGIN-TARGET",
                    content="登录接口超时",
                    metadata={"module": "login", "tags": ["smoke"]},
                ),
                KnowledgeChunkCreate(
                    source_type="testcase",
                    source_id="PAYMENT-DISTRACTOR",
                    content="登录接口超时",
                    metadata={"module": "payment", "tags": ["smoke"]},
                ),
            ],
            embedding_client=client,
        )

        results = KnowledgeChunkService.hybrid_search(
            db_session,
            query="登录接口超时",
            query_embedding=client.embed_query("登录接口超时"),
            metadata_filters={"module": "login"},
            candidate_k=2,
            limit=2,
        )

        assert [hit.source_id for hit in results] == ["LOGIN-TARGET"]

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
        assert result.citation_valid is True
        assert result.evidence_sufficient is None
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
        assert result.citation_valid is False
        assert result.evidence_sufficient is False
