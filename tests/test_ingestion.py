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
        source = "# 登录\n\n## 失败\n\n提示错误。\n"

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

    def test_reimport_replaces_chunks_without_duplicates(self, db_session) -> None:
        first = "# 登录\n\n## 失败\n\n提示错误。\n"
        second = "# 登录\n\n## 失败\n\n提示错误并保留输入。\n"

        ImportService.import_markdown(db_session, doc_id="DOC-LOGIN", source=first)
        result = ImportService.import_markdown(db_session, doc_id="DOC-LOGIN", source=second)

        assert result.created is False
        assert result.section_count == 2
        assert db_session.query(KnowledgeChunk).count() == 2
        assert "保留输入" in db_session.query(KnowledgeChunk).order_by(
            KnowledgeChunk.chunk_index
        ).all()[1].content


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
