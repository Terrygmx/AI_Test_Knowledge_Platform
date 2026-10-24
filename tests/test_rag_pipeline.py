"""Phase 18 完整 RAG Pipeline 的 trace、过滤与拒答测试。"""

from __future__ import annotations

import pytest

from app.generation.llm import LLMError
from app.generation.pipeline import RAGPipeline
from app.query.rewrite import NoOpQueryRewriter
from app.query.understanding import DeterministicQueryUnderstanding
from app.retrieval.embedding import FakeEmbeddingClient
from app.schemas.knowledge_chunk import KnowledgeChunkCreate
from app.services.knowledge_chunk_service import KnowledgeChunkService
from tests.test_rag import FakeLLM


def _seed_chunks(db_session) -> list:
    return KnowledgeChunkService.create_many(
        db_session,
        items=[
            KnowledgeChunkCreate(
                source_type="testcase",
                source_id="LOGIN-001",
                content="用例编号：LOGIN-001\n标题：登录失败\n预期结果：提示错误并保留输入",
                metadata={"module": "login"},
            ),
            KnowledgeChunkCreate(
                source_type="defect",
                source_id="BUG-001",
                content="缺陷编号：BUG-001\n标题：登录验证码失效\n根因：缓存过期",
                metadata={"module": "login"},
            ),
        ],
        embedding_client=FakeEmbeddingClient(dimension=1024),
    )


def _pipeline(llm: FakeLLM) -> RAGPipeline:
    return RAGPipeline(
        understanding=DeterministicQueryUnderstanding(),
        rewriter=NoOpQueryRewriter(),
        embedding_client=FakeEmbeddingClient(dimension=1024),
        llm=llm,
    )


def test_pipeline_returns_cited_answer_with_stage_trace(db_session) -> None:
    _seed_chunks(db_session)
    llm = FakeLLM("应检查验证码缓存是否过期。[1]")
    pipeline = _pipeline(llm)

    result = pipeline.answer(db_session, question="登录验证码失效怎么排查？")

    assert result.citation_valid is True
    assert result.citations
    assert result.evidence_sufficient is True
    trace = result.trace
    assert trace is not None
    assert trace["original_query"] == "登录验证码失效怎么排查？"
    assert trace["understanding"]["semantic_query"]
    assert trace["vector_hits"] and trace["keyword_hits"] and trace["fusion_hits"]
    assert trace["final_context"]
    assert set(trace["stage_seconds"]) >= {
        "understanding",
        "query_embedding",
        "hybrid",
        "context",
        "llm",
    }
    assert "[1]" in llm.prompts[0]


def test_pipeline_applies_understanding_filters(db_session) -> None:
    chunks = _seed_chunks(db_session)
    llm = FakeLLM("参见用例。[1]")
    pipeline = _pipeline(llm)

    result = pipeline.answer(db_session, question="login 模块的登录用例")

    assert result.citation_valid is True
    trace = result.trace
    assert trace is not None
    assert trace["filters"] == {"module": "login"}
    assert all(
        record["key"].startswith("testcase:LOGIN-001")
        or record["key"].startswith("defect:BUG-001")
        for record in trace["fusion_hits"]
    )
    assert chunks


def test_pipeline_filters_out_non_matching_module(db_session) -> None:
    _seed_chunks(db_session)
    llm = FakeLLM("不应被调用")
    pipeline = _pipeline(llm)

    result = pipeline.answer(db_session, question="payment 模块的退款规则")

    assert result.citation_valid is False
    assert result.evidence_sufficient is False
    assert result.citations == []
    assert llm.prompts == []


def test_pipeline_rejects_uncited_answer(db_session) -> None:
    _seed_chunks(db_session)
    llm = FakeLLM("不引用编号的回答")
    pipeline = _pipeline(llm)

    result = pipeline.answer(db_session, question="登录失败怎么排查？")

    assert result.citation_valid is False
    assert result.citations == []


def test_pipeline_rejects_invalid_citation_number(db_session) -> None:
    _seed_chunks(db_session)
    llm = FakeLLM("越界引用。[9]")
    pipeline = _pipeline(llm)

    with pytest.raises(LLMError, match="引用编号"):
        pipeline.answer(db_session, question="登录失败怎么排查？")


def test_pipeline_rejects_blank_question_and_invalid_top_k(db_session) -> None:
    pipeline = _pipeline(FakeLLM("x"))

    with pytest.raises(ValueError, match="空白"):
        pipeline.answer(db_session, question="   ")
    with pytest.raises(ValueError, match="top_k"):
        pipeline.answer(db_session, question="登录失败", top_k=0)
