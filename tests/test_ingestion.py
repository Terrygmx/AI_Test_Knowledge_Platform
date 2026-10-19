"""Phase 5 数据接入：表格、Markdown、PDF 与导入服务。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ingestion import (
    ImportService,
    MarkdownParser,
    PdfParser,
    TableImporter,
    infer_entity_type,
    load_table,
)
from app.models import Document, KnowledgeChunk, Requirement, TestCase
from app.retrieval.embedding import FakeEmbeddingClient
from app.services.knowledge_chunk_service import KnowledgeChunkService


def _write_csv(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


class TestTableImport:
    def test_loads_csv_with_utf8_and_normalizes_headers(self, tmp_path: Path) -> None:
        path = _write_csv(
            tmp_path / "cases.csv",
            " Case ID ,Title,Module,Priority,Tags\n"
            "LOGIN-001,短信登录,login,p0,冒烟|登录\n",
        )

        frame = load_table(path)

        assert list(frame.columns) == ["case_id", "title", "module", "priority", "tags"]
        assert frame.iloc[0]["case_id"] == "LOGIN-001"

    def test_infers_entity_type_from_columns(self) -> None:
        assert infer_entity_type(["case_id", "title", "steps"]) == "testcase"
        assert infer_entity_type(["bug_id", "title", "root_cause"]) == "defect"
        assert infer_entity_type(["req_id", "title", "content"]) == "requirement"
        assert infer_entity_type(["title", "content"]) == "document"

    def test_rejects_unknown_table_shape(self) -> None:
        with pytest.raises(ValueError, match="无法识别"):
            infer_entity_type(["foo", "bar"])

    def test_csv_import_updates_existing_and_deduplicates(self, db_session, tmp_path: Path) -> None:
        path = _write_csv(
            tmp_path / "cases.csv",
            "case_id,title,module,priority,tags\n"
            "LOGIN-001,旧标题,login,P0,冒烟|登录\n"
            "LOGIN-001,新标题,login,P1,冒烟|登录\n",
        )

        result = TableImporter.import_file(db_session, path, entity_type="testcase")

        assert result.created == 1
        assert result.updated == 0
        assert result.skipped == 1
        case = db_session.query(TestCase).one()
        assert case.title == "旧标题"

        result = TableImporter.import_file(db_session, path, entity_type="testcase")
        assert result.updated == 1
        assert result.skipped == 1
        assert db_session.query(TestCase).count() == 1
        assert db_session.query(TestCase).one().priority == "P0"

    def test_invalid_row_reports_line_and_rolls_back_file(self, db_session, tmp_path: Path) -> None:
        path = _write_csv(
            tmp_path / "bad.csv",
            "case_id,title,priority\n"
            "LOGIN-001,合法,P0\n"
            "bad id,非法,P9\n",
        )

        with pytest.raises(ValueError, match="row 3") as exc_info:
            TableImporter.import_file(db_session, path, entity_type="testcase")

        assert "priority" in str(exc_info.value)
        assert db_session.query(TestCase).count() == 0

    def test_excel_import(self, db_session, tmp_path: Path) -> None:
        import pandas as pd

        path = tmp_path / "defects.xlsx"
        pd.DataFrame(
            [
                {
                    "bug_id": "BUG-001",
                    "title": "支付失败",
                    "severity": "critical",
                    "related_cases": "PAY-001|PAY-002",
                }
            ]
        ).to_excel(path, index=False)

        result = TableImporter.import_file(db_session, path, entity_type="defect")

        assert result.created == 1
        assert db_session.query(TestCase).count() == 0

    def test_import_requirements(self, db_session, tmp_path: Path) -> None:
        path = _write_csv(
            tmp_path / "req.csv",
            "req_id,title,content,status\nREQ-001,退款需求,支持原路退回,approved\n",
        )
        result = TableImporter.import_file(db_session, path)
        assert result.created == 1
        assert db_session.query(Requirement).one().status == "approved"

    def test_document_rows_keep_body_in_chunk(self, db_session, tmp_path: Path) -> None:
        """CSV 导入文档时正文不能静默丢弃：Document 只存元数据，正文落到 Chunk。"""
        path = _write_csv(
            tmp_path / "docs.csv",
            "doc_id,title,content,doc_type\nDOC-CSV-1,支付规范,失败后允许重试三次,spec\n",
        )

        result = TableImporter.import_file(db_session, path, entity_type="document")

        assert result.created == 1
        document = db_session.query(Document).one()
        assert document.title == "支付规范"
        # ``Type`` 在文档语义下是 doc_type，不能被归一化成 case_type 后丢掉
        assert document.doc_type == "spec"
        chunk = db_session.query(KnowledgeChunk).one()
        assert chunk.source_type == "document"
        assert chunk.source_id == "DOC-CSV-1"
        assert "失败后允许重试三次" in chunk.content
        assert chunk.meta["doc_type"] == "spec"

    def test_document_rows_without_doc_id_are_rejected(self, db_session, tmp_path: Path) -> None:
        """``title + content`` 只能推断类型，落库仍需业务编号锚点。"""
        path = _write_csv(tmp_path / "docs.csv", "title,content\n支付规范,正文\n")

        with pytest.raises(ValueError, match="doc_id"):
            TableImporter.import_file(db_session, path)

        assert db_session.query(Document).count() == 0

    def test_reimporting_document_rows_reports_replaced_vectors(
        self, db_session, tmp_path: Path
    ) -> None:
        """表格重复导入同样不能静默清掉已有向量。"""
        path = _write_csv(
            tmp_path / "docs.csv",
            "doc_id,title,content\nDOC-CSV-2,支付规范,失败重试\n",
        )
        TableImporter.import_file(db_session, path, entity_type="document")
        for chunk in db_session.query(KnowledgeChunk).all():
            chunk.embedding = FakeEmbeddingClient(dimension=1024).embed_query(chunk.content)
        db_session.commit()

        result = TableImporter.import_file(db_session, path, entity_type="document")

        assert result.document_chunks == 1
        assert result.indexed_chunks_replaced == 1
        assert db_session.query(KnowledgeChunk).one().embedding is None


class TestMarkdownParser:
    def test_parses_document_tree_and_sections(self) -> None:
        source = """---
title: 支付测试规范
version: v1.3
---
# 支付

概述。

## 失败重试

失败后允许重试。

### 幂等

不得重复扣款。
"""
        doc = MarkdownParser.parse(source)

        assert doc.title == "支付测试规范"
        assert doc.version == "v1.3"
        assert [node.title for node in doc.sections] == ["支付", "失败重试", "幂等"]
        assert doc.sections[1].path == ("支付", "失败重试")
        assert "不得重复扣款" in doc.sections[2].content

    def test_keeps_code_blocks_and_tables(self) -> None:
        source = """# 规范

| 场景 | 预期 |
|---|---|
| 超时 | 重试 |

```text
retry = true
```
"""
        doc = MarkdownParser.parse(source)
        section = doc.sections[0]

        assert "| 场景 | 预期 |" in section.content
        assert "|---|---|" in section.content
        assert "retry = true" in section.content

    def test_import_creates_document_and_chunks(self, db_session) -> None:
        source = "# 登录\n\n登录概述。\n\n## 失败\n\n提示错误。\n"

        result = ImportService.import_markdown(
            db_session,
            doc_id="DOC-LOGIN",
            source=source,
            path="data/login.md",
        )

        assert result.document_id == "DOC-LOGIN"
        assert result.section_count == 2
        assert db_session.query(Document).one().doc_type == "markdown"
        chunks = db_session.query(KnowledgeChunk).all()
        assert len(chunks) == 2
        assert all(chunk.source_type == "document" for chunk in chunks)

    def test_document_without_heading_keeps_whole_body(self, db_session) -> None:
        """无标题文档（典型：纯文本 PDF 抽取结果）必须整篇作为一个 Section。

        否则导入「成功」却 0 个 Chunk，正文整体丢失且没有任何报错。
        """
        source = "纯文本第一段。\n第二段内容。\n"

        result = ImportService.import_markdown(db_session, doc_id="DOC-NOTITLE", source=source)

        assert result.section_count == 1
        chunk = db_session.query(KnowledgeChunk).one()
        assert chunk.content == "纯文本第一段。\n第二段内容。"
        assert db_session.query(Document).one().content_hash != (
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )

    def test_preamble_before_first_heading_is_kept(self) -> None:
        """首个标题之前的“前言”正文不能被丢弃。"""
        doc = MarkdownParser.parse("文档开头摘要。\n\n# 第一章\n\n正文。\n")

        assert [section.title for section in doc.sections][0] == "前言"
        assert doc.sections[0].content == "文档开头摘要。"
        assert doc.sections[1].title == "第一章"
        assert doc.title == "第一章"

    def test_empty_heading_merges_body_into_previous_section(self) -> None:
        """空标题（``## ``）没有语义锚点，但正文不能被连带丢弃。"""
        doc = MarkdownParser.parse("# A\n\n正文。\n\n## \n\n空标题正文。\n")

        assert [section.title for section in doc.sections] == ["A"]
        assert doc.sections[0].content == "正文。\n\n空标题正文。"

    def test_empty_body_section_uses_heading_path_as_content(self) -> None:
        """``# A`` 紧接 ``## B`` 时 A 没有正文，用标题路径兜底而不是留空 Chunk。

        直接丢弃会让该标题彻底不可检索；空 Chunk 又无法被 Embedding
        （上游拒绝空白输入），所以标题路径是唯一可检索的最小表示。
        """
        doc = MarkdownParser.parse("# A\n## B\n\n正文。\n")

        assert [section.title for section in doc.sections] == ["A", "B"]
        assert doc.sections[0].content == "A"
        assert doc.sections[1].content == "正文。"

    def test_reimport_reports_replaced_indexed_chunks(self, db_session) -> None:
        """重复导入会清掉已有向量，必须把这件事显式返回给调用方。"""
        ImportService.import_markdown(db_session, doc_id="DOC-VEC", source="# 标题\n正文\n")
        for chunk in db_session.query(KnowledgeChunk).all():
            chunk.embedding = FakeEmbeddingClient(dimension=1024).embed_query(chunk.content)
        db_session.commit()

        result = ImportService.import_markdown(db_session, doc_id="DOC-VEC", source="# 标题\n新正文\n")

        assert result.indexed_chunks_replaced == 1
        assert db_session.query(KnowledgeChunk).one().embedding is None

    def test_reimport_replaces_chunks_without_duplicates(self, db_session) -> None:
        first = "# 登录\n\n登录概述。\n\n## 失败\n\n提示错误。\n"
        second = "# 登录\n\n登录概述。\n\n## 失败\n\n提示错误并保留输入。\n"

        ImportService.import_markdown(db_session, doc_id="DOC-LOGIN", source=first)
        result = ImportService.import_markdown(db_session, doc_id="DOC-LOGIN", source=second)

        assert result.created is False
        assert result.section_count == 2
        assert result.indexed_chunks_replaced == 0
        assert db_session.query(KnowledgeChunk).count() == 2
        assert "保留输入" in db_session.query(KnowledgeChunk).order_by(
            KnowledgeChunk.chunk_index
        ).all()[1].content

    def test_document_chunks_can_be_backfilled_with_embeddings(self, db_session) -> None:
        """document Chunk 必须有补向量入口，否则永远进不了向量检索。"""
        ImportService.import_markdown(db_session, doc_id="DOC-BF", source="# 支付\n失败重试。\n")
        assert db_session.query(KnowledgeChunk).one().embedding is None

        backfilled = KnowledgeChunkService.backfill_embeddings(
            db_session,
            embedding_client=FakeEmbeddingClient(dimension=1024),
            source_types=["document"],
        )

        assert backfilled == 1
        assert db_session.query(KnowledgeChunk).one().embedding is not None
        assert KnowledgeChunkService.count_without_embedding(db_session) == 0


class TestPdfParser:
    def test_extracts_plain_text(self, tmp_path: Path, monkeypatch) -> None:
        class Page:
            def extract_text(self):
                return "第一页内容"

        class Reader:
            def __init__(self, path):
                self.pages = [Page()]

        monkeypatch.setattr("app.ingestion.pdf.PdfReader", Reader)
        text = PdfParser.parse(tmp_path / "spec.pdf")
        assert text == "第一页内容"

    def test_empty_pdf_is_rejected(self, tmp_path: Path, monkeypatch) -> None:
        class Reader:
            def __init__(self, path):
                self.pages = []

        monkeypatch.setattr("app.ingestion.pdf.PdfReader", Reader)
        with pytest.raises(ValueError, match="没有可提取文本"):
            PdfParser.parse(tmp_path / "empty.pdf")

    def test_pdf_without_headings_still_creates_chunks(self, db_session, tmp_path, monkeypatch) -> None:
        """无标题的纯文本 PDF 抽取结果不能落得 0 个 Chunk。"""

        class Page:
            def extract_text(self):
                return "第一章正文。\n\n第二章正文。"

        class Reader:
            def __init__(self, path):
                self.pages = [Page()]

        monkeypatch.setattr("app.ingestion.pdf.PdfReader", Reader)
        path = tmp_path / "spec.pdf"
        path.write_bytes(b"%PDF-1.4")

        result = ImportService.import_pdf(db_session, doc_id="DOC-PDF", path=path)

        assert result.section_count == 1
        chunk = db_session.query(KnowledgeChunk).one()
        assert "第一章正文。" in chunk.content
        assert chunk.meta["title"] == "spec"
