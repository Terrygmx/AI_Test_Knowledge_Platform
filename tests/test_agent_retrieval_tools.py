"""Phase 25 真实检索工具测试：证据带来源、失败返回错误不抛异常。"""

from __future__ import annotations

from app.agent.retrieval_tools import (
    CompareVersionsTool,
    HybridSearchTool,
    SearchDefectsTool,
    SearchTestCasesTool,
)
from app.agent.tools import build_default_registry
from app.models import Defect, TestCase
from app.retrieval.embedding import FakeEmbeddingClient


def _seed(db_session) -> None:
    db_session.add_all(
        [
            TestCase(case_id="LOGIN-001", title="登录失败排查", steps="打开登录页", expected_result="提示错误"),
            Defect(bug_id="BUG-001", title="验证码失效", root_cause="缓存过期"),
        ]
    )
    db_session.commit()


def test_default_registry_contains_whitelist_tools(db_session) -> None:
    registry = build_default_registry(db_session)

    assert registry.names == [
        "compare_versions",
        "hybrid_search",
        "search_defects",
        "search_documents",
        "search_requirements",
        "search_testcases",
    ]


def test_entity_search_returns_sourced_evidence(db_session) -> None:
    _seed(db_session)
    tool = SearchTestCasesTool(db_session)

    result = tool.run({"query": "登录"})

    assert result.ok is True
    assert len(result.evidence) == 1
    assert result.evidence[0].startswith("testcase:LOGIN-001")
    assert "登录" in result.evidence[0]


def test_entity_search_blank_query_is_tool_error(db_session) -> None:
    tool = SearchDefectsTool(db_session)

    result = tool.run({"query": "  "})

    assert result.ok is False
    assert "query" in result.errors[0]


def test_hybrid_search_on_sqlite_returns_tool_error_not_exception(db_session) -> None:
    """SQLite 无向量通道：失败必须转成 ToolResult，不能炸掉 Agent 循环。

    注意本机 OMLX 可达时 vector 通道能成功（0 hits 也是成功），
    所以两种结果都合法；关键是不能抛异常。
    """
    tool = HybridSearchTool(db_session)

    result = tool.run({"query": "登录"})

    assert isinstance(result.errors, list)


def test_hybrid_search_limit_out_of_range_is_error(db_session, monkeypatch) -> None:
    _seed(db_session)

    from app.agent import retrieval_tools

    monkeypatch.setattr(
        retrieval_tools,
        "get_embedding_client",
        lambda: FakeEmbeddingClient(dimension=1024),
    )
    tool = HybridSearchTool(db_session)
    # PG 才能走到 limit 校验之后的检索；SQLite 在向量通道就报错。
    # 这里只验证 limit 非法在任何方言下都返回错误结果。
    result = tool.run({"query": "登录", "limit": 0})

    assert result.ok is False


def test_compare_versions_requires_both_versions(db_session) -> None:
    tool = CompareVersionsTool(db_session)

    result = tool.run({"base_version": "v2.3"})

    assert result.ok is False
    assert "base_version" in result.errors[0] or "target_version" in result.errors[0]


def test_compare_versions_rejects_invalid_version(db_session) -> None:
    tool = CompareVersionsTool(db_session)

    result = tool.run({"base_version": "2.3", "target_version": "v2.4"})

    assert result.ok is False
    assert result.errors
