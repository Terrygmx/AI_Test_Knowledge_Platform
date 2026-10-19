"""索引脚本的离线逻辑测试。"""

from __future__ import annotations

import pytest

from app.models import Defect, TestCase
from app.retrieval.embedding import EmbeddingError, FakeEmbeddingClient
from app.schemas.knowledge_chunk import KnowledgeChunkCreate
from app.services.knowledge_chunk_service import KnowledgeChunkService
from scripts.index_entities import _parse_only, rebuild


class TestIndexScript:
    def test_parse_only_rejects_unknown(self) -> None:
        with pytest.raises(ValueError, match="未知实体类型"):
            _parse_only("testcase,nope")

    def test_parse_only_accepts_document(self) -> None:
        assert _parse_only("document") == {"document"}

    def test_dry_run_counts_without_writing(self, db_session) -> None:
        db_session.add_all(
            [
                TestCase(case_id="IDX-1", title="登录"),
                Defect(bug_id="IDX-BUG-1", title="支付失败"),
            ]
        )
        db_session.commit()

        stats = rebuild(db_session, only={"testcase", "defect"}, dry_run=True)
        assert stats.testcase == 1
        assert stats.defect == 1

        from app.models import KnowledgeChunk

        assert db_session.query(KnowledgeChunk).count() == 0

    def test_rebuild_keeps_old_index_when_embedding_fails(
        self, db_session, monkeypatch
    ) -> None:
        from app.models import KnowledgeChunk

        KnowledgeChunkService.create(
            db_session,
            data=KnowledgeChunkCreate(
                source_type="testcase",
                source_id="IDX-OLD",
                content="旧索引",
            ),
            embedding_client=FakeEmbeddingClient(dimension=1024),
        )
        db_session.add(TestCase(case_id="IDX-NEW", title="新索引"))
        db_session.commit()

        class FailingEmbeddingClient:
            @property
            def dimension(self) -> int:
                return 1024

            def embed_texts(self, texts):
                raise EmbeddingError("模拟 Embedding 上游失败")

            def embed_query(self, text):
                raise AssertionError("本用例不应调用 embed_query")

        def failing_client():
            return FailingEmbeddingClient()

        monkeypatch.setattr("scripts.index_entities.get_embedding_client", failing_client)

        with pytest.raises(EmbeddingError, match="模拟"):
            rebuild(db_session, only={"testcase"}, dry_run=False)

        db_session.rollback()
        assert db_session.query(KnowledgeChunk).count() == 1
