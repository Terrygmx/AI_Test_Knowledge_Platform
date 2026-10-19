#!/usr/bin/env python3
"""Phase 5 数据接入入口。

用法：
    uv run python scripts/ingest.py table data/cases.csv
    uv run python scripts/ingest.py table data/cases.csv --type testcase
    uv run python scripts/ingest.py markdown data/spec.md --doc-id DOC-001
    uv run python scripts/ingest.py pdf data/spec.pdf --doc-id DOC-002
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import SessionLocal
from app.ingestion import ImportService, TableImporter


def _print_embedding_tips(indexed_chunks_replaced: int) -> None:
    """导入阶段刻意不调用 Embedding，所以必须把「还不能检索」说清楚。

    大批量导入与模型服务耦合会让一次导入既慢又难重试，向量生成交给
    ``index_entities.py``；代价是调用方很容易忘记这一步，这里显式提示。
    """
    print(
        "提示：Chunk 尚未生成向量，还不能参与检索；"
        "执行 scripts/index_entities.py --only document 补齐（增量，不重建正文）"
    )
    if indexed_chunks_replaced:
        print(
            f"注意：本次导入替换了 {indexed_chunks_replaced} 条已向量化的 Chunk，"
            "必须重新执行 scripts/index_entities.py --only document 补向量"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="导入测试知识数据")
    subparsers = parser.add_subparsers(dest="command", required=True)

    table = subparsers.add_parser("table", help="导入 CSV / Excel")
    table.add_argument("path")
    table.add_argument(
        "--type",
        choices=["testcase", "defect", "requirement", "document"],
        default=None,
        help="不传时按表头自动识别",
    )

    markdown = subparsers.add_parser("markdown", help="导入 Markdown 文档")
    markdown.add_argument("path")
    markdown.add_argument("--doc-id", required=True)
    markdown.add_argument("--version", default=None)

    pdf = subparsers.add_parser("pdf", help="导入普通文本 PDF")
    pdf.add_argument("path")
    pdf.add_argument("--doc-id", required=True)
    pdf.add_argument("--version", default=None)

    args = parser.parse_args()
    path = Path(args.path)
    if not path.is_file():
        parser.error(f"文件不存在：{path}")

    with SessionLocal() as session:
        if args.command == "table":
            result = TableImporter.import_file(session, path, entity_type=args.type)
            print(
                f"导入完成：type={result.entity_type} created={result.created} "
                f"updated={result.updated} skipped={result.skipped}"
                + (
                    f" document_chunks={result.document_chunks}"
                    if result.entity_type == "document"
                    else ""
                )
            )
            if result.entity_type == "document" and result.document_chunks:
                _print_embedding_tips(result.indexed_chunks_replaced)
            return

        if args.command == "markdown":
            result = ImportService.import_markdown(
                session,
                doc_id=args.doc_id,
                source=path.read_text(encoding="utf-8"),
                path=str(path),
                version=args.version,
            )
        else:
            result = ImportService.import_pdf(
                session,
                doc_id=args.doc_id,
                path=path,
                version=args.version,
            )
        action = "创建" if result.created else "更新"
        print(
            f"导入完成：{action} document={result.document_id} "
            f"sections={result.section_count}"
        )
        if result.section_count == 0:
            print(
                "注意：该文档没有解析出任何正文，已保留原有 Document 与 Chunk；"
                "请确认源文件是否为空或格式不受支持"
            )
            return
        _print_embedding_tips(result.indexed_chunks_replaced)


if __name__ == "__main__":
    main()
