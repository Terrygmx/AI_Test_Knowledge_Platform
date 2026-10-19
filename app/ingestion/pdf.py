"""普通文本 PDF 抽取；OCR / 表格 / 版面还原不在 Phase 5 范围。"""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader


class PdfParser:
    """只抽取 PDF 的文本层，不猜测版面结构。"""

    @classmethod
    def parse(cls, path: str | Path) -> str:
        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        text = "\n\n".join(page.strip() for page in pages if page.strip()).strip()
        if not text:
            raise ValueError(f"PDF 没有可提取文本：{path}")
        return text


__all__ = ["PdfParser"]
