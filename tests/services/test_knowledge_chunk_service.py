"""KnowledgeChunk 服务与实体索引测试。"""

from __future__ import annotations

import time

import pytest

from app.core.exceptions import DomainError
from app.models import Defect, Document, KnowledgeChunk, Requirement, TestCase
from app.retrieval.embedding import EmbeddingError, FakeEmbeddingClient, content_sha256
from app.retrieval.hybrid import FusedHit
from app.schemas.knowledge_chunk import KnowledgeChunkCreate
from app.services.index_service import IndexService, case_to_chunk
from app.services.knowledge_chunk_service import KnowledgeChunkService


class TestKnowledgeChunkCreate:
    def test_lexical_search_works_on_sqlite(self, db_session) -> None:
        for index, (source_type, source_id, content) in enumerate(
            [
                ("defect", "BUG-1234", "P0 BUG-1234 login-api v1.3 登录接口超时"),
                ("testcase", "TC-1", "登录接口验证码校验"),
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
        assert results[0][1] > 0
        assert "bug-1234" in results[0][2]

    def test_lexical_search_filters_source_types_and_limit(self, db_session) -> None:
        for index, (source_type, source_id, content) in enumerate(
            [
                ("defect", "BUG-1", "登录接口超时"),
                ("testcase", "TC-1", "登录接口超时"),
                ("document", "DOC-1", "登录接口超时"),
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
            query="登录接口超时",
            source_types=["testcase", "document"],
            limit=1,
        )

        assert len(results) == 1
        assert results[0][0].source_type in {"testcase", "document"}

    def test_lexical_search_rejects_non_positive_limit(self, db_session) -> None:
        with pytest.raises(ValueError, match="limit"):
            KnowledgeChunkService.lexical_search(db_session, query="登录", limit=0)

    @pytest.mark.parametrize("query", ["", "   \n"])
    def test_lexical_search_returns_empty_for_blank_query(self, db_session, query) -> None:
        KnowledgeChunkService.create(
            db_session,
            data=KnowledgeChunkCreate(
                source_type="testcase",
                source_id="TC-BLANK",
                content="登录接口验证码校验",
            ),
        )

        assert KnowledgeChunkService.lexical_search(db_session, query=query) == []

    def test_lexical_search_returns_empty_for_unknown_source_type(self, db_session) -> None:
        KnowledgeChunkService.create(
            db_session,
            data=KnowledgeChunkCreate(
                source_type="testcase",
                source_id="TC-FILTER",
                content="登录接口验证码校验",
            ),
        )

        assert (
            KnowledgeChunkService.lexical_search(
                db_session,
                query="登录接口",
                source_types=["unknown"],
            )
            == []
        )

    def test_lexical_search_one_thousand_chunks_stays_within_loose_budget(
        self, db_session
    ) -> None:
        """守护 SQL 全量读取 + ORM 转换 + 建索引的小规模边界，不是性能基准。"""
        chunks = []
        for index in range(1_000):
            content = f"登录接口超时 第 {index} 条记录，支付状态待确认"
            chunks.append(
                KnowledgeChunk(
                    source_type="document",
                    source_id=f"SCALE-{index // 10:04d}",
                    chunk_index=index % 10,
                    content=content,
                    content_hash=content_sha256(content),
                )
            )
        db_session.add_all(chunks)
        db_session.commit()

        started = time.perf_counter()
        results = KnowledgeChunkService.lexical_search(
            db_session,
            query="登录接口超时",
            limit=5,
        )
        elapsed = time.perf_counter() - started

        assert len(results) == 5
        assert elapsed < 20.0

    def test_hybrid_search_is_available_on_postgresql(self, engine, db_session) -> None:
        """Phase 9 的 Chunk Hybrid 接缝：Vector + BM25 融合，身份保留 chunk_index。"""
        if engine.dialect.name != "postgresql":
            pytest.skip("PostgreSQL + pgvector only")

        embedding = FakeEmbeddingClient(dimension=1024)
        for chunk_index, content in enumerate(
            [
                "P0 BUG-1234 login-api v1.3 登录接口超时",
                "登录接口验证码校验",
            ]
        ):
            KnowledgeChunkService.create(
                db_session,
                data=KnowledgeChunkCreate(
                    source_type="defect",
                    source_id="BUG-1234",
                    chunk_index=chunk_index,
                    content=content,
                ),
                embedding_client=embedding,
            )

        results = KnowledgeChunkService.hybrid_search(
            db_session,
            query="P0 BUG-1234 login-api v1.3",
            query_embedding=embedding.embed_query("登录接口超时"),
            candidate_k=2,
            limit=2,
        )

        assert len(results) == 2
        assert all(isinstance(item, FusedHit) for item in results)
        assert {item.chunk_index for item in results} == {0, 1}
        assert results[0].vector_rank is not None
        assert results[0].keyword_rank is not None

    def test_hybrid_search_rejects_sqlite_without_faking_scores(self, engine, db_session) -> None:
        if engine.dialect.name == "postgresql":
            pytest.skip("SQLite-only check")
        with pytest.raises(DomainError, match="PostgreSQL"):
            KnowledgeChunkService.hybrid_search(
                db_session,
                query="登录",
                query_embedding=[0.0] * 1024,
            )

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"candidate_k": 0}, "candidate_k"),
            ({"limit": 0}, "limit"),
            ({"rrf_k": 0}, "rrf_k"),
            ({"candidate_k": 2, "limit": 3}, "不能小于"),
            ({"query": "   "}, "query"),
        ],
    )
    def test_hybrid_search_rejects_invalid_configuration_before_query(
        self, db_session, kwargs, message
    ) -> None:
        arguments = {
            "query": "登录",
            "query_embedding": [0.0] * 1024,
            **kwargs,
        }
        with pytest.raises((DomainError, ValueError), match=message):
            KnowledgeChunkService.hybrid_search(db_session, **arguments)

    def test_backfill_commits_completed_batches_when_upstream_fails(self, db_session) -> None:
        """上游中途失败时已补批次不回滚，否则大批量补向量等于全或无。"""
        for index in range(4):
            KnowledgeChunkService.create(
                db_session,
                data=KnowledgeChunkCreate(
                    source_type="document",
                    source_id="BF-1",
                    chunk_index=index,
                    content=f"第 {index} 段正文",
                ),
            )

        calls: list[int] = []

        class FlakyEmbeddingClient:
            """第二批失败：前两批必须已经落库。"""

            dimension = 1024

            def embed_texts(self, texts):
                calls.append(len(texts))
                if len(calls) > 1:
                    raise EmbeddingError("模拟第二批上游失败")
                return [FakeEmbeddingClient(dimension=1024).embed_query(text) for text in texts]

            def embed_query(self, text):
                return FakeEmbeddingClient(dimension=1024).embed_query(text)

        with pytest.raises(EmbeddingError, match="第二批"):
            KnowledgeChunkService.backfill_embeddings(
                db_session,
                embedding_client=FlakyEmbeddingClient(),
                source_types=["document"],
                batch_size=2,
            )

        assert calls == [2, 2]
        assert KnowledgeChunkService.count_without_embedding(db_session) == 2

    def test_reembed_chunks_refreshes_existing_vectors(self, db_session) -> None:
        """切分策略 / 模型变更后必须有显式重算入口，而不只是补空缺。"""
        chunk = KnowledgeChunkService.create(
            db_session,
            data=KnowledgeChunkCreate(
                source_type="document",
                source_id="BF-2",
                content="旧正文",
            ),
            embedding_client=FakeEmbeddingClient(dimension=1024),
        )
        original = list(chunk.embedding or [])

        class SaltedFakeEmbeddingClient:
            """同一个 Fake 但带盐：模拟「换了模型 / 换了切分后重算」。"""

            dimension = 1024

            def embed_texts(self, texts):
                return [self.embed_query(text) for text in texts]

            def embed_query(self, text):
                return FakeEmbeddingClient(dimension=1024).embed_query(f"v2::{text}")

        written = KnowledgeChunkService.reembed_chunks(
            db_session,
            embedding_client=SaltedFakeEmbeddingClient(),
            source_types=["document"],
        )

        assert written == 1
        assert list(chunk.embedding or []) != original
        # 补向量入口只补空缺，重算入口才刷新已有向量
        assert KnowledgeChunkService.backfill_embeddings(
            db_session,
            embedding_client=FakeEmbeddingClient(dimension=1024),
            source_types=["document"],
        ) == 0

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
            sub_module="sms",
            priority="P0",
            case_type="smoke",
        )
        chunk = case_to_chunk(case)
        assert chunk.source_type == "testcase"
        assert chunk.source_id == "LOGIN-001"
        assert "短信登录冒烟" in chunk.content
        assert chunk.metadata["priority"] == "P0"
        assert chunk.metadata["sub_module"] == "sms"
        assert chunk.metadata["case_type"] == "smoke"
        assert chunk.metadata["tags"] == ["smoke"]

    def test_defect_and_requirement_mapping(self) -> None:
        from app.services.index_service import defect_to_chunk, requirement_to_chunk

        defect_chunk = defect_to_chunk(
            Defect(
                bug_id="BUG-1",
                title="支付失败",
                root_cause="超时",
                related_cases=["PAY-001"],
            )
        )
        assert defect_chunk.source_type == "defect"
        assert "根因：超时" in defect_chunk.content
        assert defect_chunk.metadata["related_cases"] == ["PAY-001"]

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
