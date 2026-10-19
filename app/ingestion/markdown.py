"""Markdown 文档树解析：Heading → Sections → Chunks。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from markdown_it import MarkdownIt


@dataclass(frozen=True)
class MarkdownSection:
    """一个标题及其直属正文。"""

    title: str
    level: int
    path: tuple[str, ...]
    content: str


@dataclass(frozen=True)
class MarkdownDocument:
    """解析后的文档树。"""

    title: str
    version: str | None
    metadata: dict[str, str]
    sections: list[MarkdownSection] = field(default_factory=list)


class MarkdownParser:
    """解析 Markdown 标题、正文、表格与代码块。"""

    _FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)

    @classmethod
    def parse(cls, source: str, *, fallback_title: str = "未命名文档") -> MarkdownDocument:
        metadata: dict[str, str] = {}
        body = source
        match = cls._FRONT_MATTER.match(source)
        if match:
            body = source[match.end() :]
            for line in match.group(1).splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                metadata[key.strip().lower()] = value.strip()

        # 用 token 只识别标题；正文按原始行切片，保证表格 / 代码块 / 列表不被
        # Markdown renderer 改写。标题行本身由 token 的 line map 定位。
        tokens = MarkdownIt("commonmark", {"html": False}).parse(body)
        headings: list[tuple[int, int, str]] = []
        for index, token in enumerate(tokens):
            if token.type != "heading_open":
                continue
            inline = tokens[index + 1] if index + 1 < len(tokens) else None
            if inline is None or inline.type != "inline":
                continue
            headings.append((token.map[0], int(token.tag[1:]), inline.content.strip()))

        lines = body.splitlines()
        sections: list[MarkdownSection] = []
        heading_stack: list[tuple[int, str]] = []
        for position, (start, level, title) in enumerate(headings):
            end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
            content = "\n".join(lines[start + 1 : end]).strip()
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
            sections.append(
                MarkdownSection(
                    title=title,
                    level=level,
                    path=tuple(item[1] for item in heading_stack),
                    content=content,
                )
            )

        title = metadata.get("title") or (sections[0].title if sections else fallback_title)
        return MarkdownDocument(
            title=title,
            version=metadata.get("version"),
            metadata=metadata,
            sections=[section for section in sections if section.title],
        )


__all__ = ["MarkdownDocument", "MarkdownParser", "MarkdownSection"]
