"""Phase 5 CLI 入口的离线测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.models import KnowledgeChunk
from app.retrieval.embedding import FakeEmbeddingClient
from scripts import ingest


def test_rejects_missing_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["ingest.py", "table", str(tmp_path / "missing.csv")],
    )
    with pytest.raises(SystemExit) as exc:
        ingest.main()
    assert exc.value.code == 2


def test_table_document_output_mentions_backfill(
    monkeypatch, session_factory, tmp_path: Path, capsys
) -> None:
    """document 行落库后必须提示「还没有向量」，否则用户以为已可检索。

    CLI 默认连开发库（``SessionLocal``），这里替换成测试库会话工厂。
    """
    monkeypatch.setattr(ingest, "SessionLocal", session_factory)
    path = tmp_path / "docs.csv"
    path.write_text("doc_id,title,content\nDOC-CLI-1,支付规范,失败重试。\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["ingest.py", "table", str(path)])

    ingest.main()

    out = capsys.readouterr().out
    assert "document_chunks=1" in out
    assert "尚未生成向量" in out
    assert "index_entities.py --only document" in out


def test_markdown_output_reports_replaced_vectors(
    monkeypatch, session_factory, tmp_path: Path, capsys
) -> None:
    """重复导入 Markdown 时要明确告知已有向量被清掉。"""
    monkeypatch.setattr(ingest, "SessionLocal", session_factory)
    path = tmp_path / "spec.md"
    path.write_text("# 登录\n\n正文。\n", encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv", ["ingest.py", "markdown", str(path), "--doc-id", "DOC-CLI-2"]
    )

    ingest.main()
    with session_factory() as session:
        chunk = session.query(KnowledgeChunk).one()
        vector = chunk.embedding
        assert vector is None
        chunk.embedding = FakeEmbeddingClient(dimension=1024).embed_query(chunk.content)
        session.commit()
    capsys.readouterr()
    ingest.main()

    out = capsys.readouterr().out
    assert "更新" in out
    assert "sections=1" in out
    assert "替换了 1 条已向量化的 Chunk" in out


def test_markdown_output_warns_when_nothing_parsed(
    monkeypatch, session_factory, tmp_path: Path, capsys
) -> None:
    """空文档不能悄悄清掉已有 Chunk / 元数据，CLI 必须说清楚。"""
    monkeypatch.setattr(ingest, "SessionLocal", session_factory)
    path = tmp_path / "empty.md"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv", ["ingest.py", "markdown", str(path), "--doc-id", "DOC-CLI-3"]
    )

    ingest.main()

    out = capsys.readouterr().out
    assert "sections=0" in out
    assert "保留原有 Document 与 Chunk" in out
    assert "尚未生成向量" not in out
