"""Markdown 文档树解析：Heading → Sections → Chunks。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

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


def _with_fallback_content(section: MarkdownSection) -> MarkdownSection:
    """空正文的 Section 用「标题路径」兜底。

    ``# A`` 紧接 ``## B`` 时 A 没有正文；直接丢弃会让该标题彻底不可检索，
    而空 Chunk 又无法被 Embedding（上游会拒绝空白输入）。取标题路径作正文：
    标题本身就是有效的检索锚点，且不会产生空向量。
    """
    if section.content.strip():
        return section
    return MarkdownSection(
        title=section.title,
        level=section.level,
        path=section.path,
        content=" / ".join(section.path) or section.title,
    )


class MarkdownParser:
    """解析 Markdown 标题、正文、表格与代码块。"""

    _FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
    #: 没有标题时的整篇 Section 标题；也是首个标题之前“前言”的标题
    _FALLBACK_SECTION_TITLE = "前言"

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
        first_heading = next((title for _, _, title in headings if title.strip()), None)
        document_title = metadata.get("title") or first_heading or fallback_title

        if not headings:
            # 无标题文档（典型场景：纯文本 PDF 抽取结果）整篇作为一个 Section，
            # 否则正文会被静默丢弃，导入“成功”却 0 个 Chunk。
            content = "\n".join(lines).strip()
            if content:
                sections.append(
                    MarkdownSection(
                        title=document_title,
                        level=1,
                        path=(document_title,),
                        content=content,
                    )
                )
        else:
            # 首个标题之前的正文（前言）同样保留，避免被丢弃。
            preamble_end = headings[0][0]
            preamble = "\n".join(lines[:preamble_end]).strip()
            if preamble:
                sections.append(
                    MarkdownSection(
                        title=cls._FALLBACK_SECTION_TITLE,
                        level=1,
                        path=(cls._FALLBACK_SECTION_TITLE,),
                        content=preamble,
                    )
                )
            for position, (start, level, title) in enumerate(headings):
                end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
                content = "\n".join(lines[start + 1 : end]).strip()
                cls._add_section(
                    sections,
                    heading_stack,
                    title=title,
                    level=level,
                    content=content,
                    document_title=document_title,
                )

        return MarkdownDocument(
            title=document_title,
            version=metadata.get("version"),
            metadata=metadata,
            sections=[_with_fallback_content(section) for section in sections],
        )

    @staticmethod
    def _add_section(
        sections: list[MarkdownSection],
        heading_stack: list[tuple[int, str]],
        *,
        title: str,
        level: int,
        content: str,
        document_title: str,
    ) -> None:
        """追加一个 Section；空标题的正文并入上一个 Section。

        ``## ``（空标题）没有语义锚点，早先版本会连带正文一起丢弃。这里改为
        把正文并入上一个 Section（同级延续）；首个标题就为空时落到文档标题下。
        """
        if not title:
            if not content:
                return
            if sections:
                previous = sections[-1]
                sections[-1] = replace(
                    previous,
                    content=(
                        f"{previous.content}\n\n{content}" if previous.content else content
                    ),
                )
                return
            title = document_title

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


__all__ = ["MarkdownDocument", "MarkdownParser", "MarkdownSection"]
