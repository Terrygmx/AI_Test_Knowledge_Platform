"""Phase 6 文档切分策略。

这里只处理**文档正文**。TestCase / Defect / Requirement 的实体级表示仍由
``IndexService`` 负责：结构化字段先拼成一段语义完整的证据，再作为一个检索单元。

固定长度策略以字符数衡量。中文场景没有稳定、与模型无关的 Token 计数方式，
按字符切分更容易复现实验；切分点仍尽量靠近段落 / 句子边界，避免把一句话劈开。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.retrieval.embedding import EmbeddingClient

#: 句子边界：中英文句号、问号、感叹号、分号后紧跟空白或文本结束
_SENTENCE_END = re.compile(r"(?<=[。！？!?；;])\s*|\n+")


@dataclass(frozen=True)
class ChunkDraft:
    """一个待落库的文档片段；``start/end`` 是原文字符偏移，便于引用与调试。"""

    content: str
    start: int
    end: int


class DocumentChunker(Protocol):
    """文档切分策略接口。"""

    name: str

    def split(self, text: str) -> list[ChunkDraft]: ...


def _split_units(text: str) -> list[tuple[str, int, int]]:
    """按段落优先、句子其次拆成不可再分的文本单元。"""
    units: list[tuple[str, int, int]] = []
    for paragraph_match in re.finditer(r".+?(?:\n\s*\n|$)", text, re.DOTALL):
        paragraph = paragraph_match.group(0)
        paragraph_start = paragraph_match.start()
        paragraph_end = paragraph_match.end()
        stripped = paragraph.strip()
        if not stripped:
            continue
        leading = len(paragraph) - len(paragraph.lstrip())
        paragraph_cursor = paragraph_start + leading
        sentence_parts = [part for part in _SENTENCE_END.split(stripped) if part]
        for sentence in sentence_parts:
            sentence = sentence.strip()
            if not sentence:
                continue
            position = text.find(sentence, paragraph_cursor, paragraph_end)
            if position < 0:
                position = paragraph_cursor
            units.append((sentence, position, position + len(sentence)))
            paragraph_cursor = position + len(sentence)
    if not units and text.strip():
        stripped = text.strip()
        start = text.find(stripped)
        return [(stripped, start, start + len(stripped))]
    return units


def _pack_units(
    units: Sequence[tuple[str, int, int]],
    *,
    max_chars: int,
    overlap_chars: int,
) -> list[ChunkDraft]:
    """把不可再分单元打包成不超过 ``max_chars`` 的 Chunk。

    单句超过上限时按字符硬切；重叠只复制上一块的尾部文本，不改变原文偏移语义。
    """
    if max_chars < 1:
        raise ValueError("max_chars 必须 >= 1")
    if overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("overlap_chars 必须满足 0 <= overlap < max_chars")

    expanded: list[tuple[str, int, int]] = []
    for content, start, end in units:
        if len(content) <= max_chars:
            expanded.append((content, start, end))
            continue
        for offset in range(0, len(content), max_chars):
            piece = content[offset : offset + max_chars]
            expanded.append((piece, start + offset, start + offset + len(piece)))

    chunks: list[ChunkDraft] = []
    current: list[tuple[str, int, int]] = []
    current_len = 0

    def add_chunk(parts: list[tuple[str, int, int]]) -> None:
        content = "\n".join(item[0] for item in parts).strip()
        if content:
            chunks.append(
                ChunkDraft(
                    content=content,
                    start=parts[0][1],
                    end=parts[-1][2],
                )
            )

    def flush() -> None:
        nonlocal current, current_len
        if not current:
            return
        add_chunk(current)
        current = []
        current_len = 0

    for unit in expanded:
        content = unit[0]
        extra = len(content) + (1 if current else 0)
        if current and current_len + extra > max_chars:
            flush()
        current.append(unit)
        current_len += len(content) + (1 if len(current) > 1 else 0)
    flush()

    if overlap_chars == 0 or len(chunks) < 2:
        return chunks

    # 重叠只取「前一个原文 Chunk」的尾部，不链式复制，避免重叠无限累积。
    # 追加后若超过 max_chars，则不再追加，保证字符上限是硬约束。
    # span 仍指向当前 Chunk 的非重叠主体：重叠前缀是检索上下文，不是新的原文范围。
    overlapped: list[ChunkDraft] = [chunks[0]]
    for previous, chunk in zip(chunks[:-1], chunks[1:], strict=True):
        tail = previous.content[-overlap_chars:].strip()
        content = f"{tail}\n{chunk.content}" if tail else chunk.content
        if len(content) <= max_chars:
            overlapped.append(
                ChunkDraft(content=content, start=chunk.start, end=chunk.end)
            )
        else:
            overlapped.append(chunk)
    return overlapped


@dataclass(frozen=True)
class FixedChunker:
    """固定字符上限 + 可选重叠；在句子 / 段落边界处尽量自然收口。"""

    max_chars: int = 512
    overlap_chars: int = 64
    name: str = "fixed"

    def __post_init__(self) -> None:
        if self.max_chars < 1:
            raise ValueError("max_chars 必须 >= 1")
        if not 0 <= self.overlap_chars < self.max_chars:
            raise ValueError("overlap_chars 必须满足 0 <= overlap < max_chars")

    def split(self, text: str) -> list[ChunkDraft]:
        if not text.strip():
            return []
        return _pack_units(
            _split_units(text),
            max_chars=self.max_chars,
            overlap_chars=self.overlap_chars,
        )


@dataclass(frozen=True)
class SemanticChunker:
    """在低相似度处切分的语义 Chunker。

    先按段落 / 句子形成小单元，再用 Embedding 比较相邻窗口的 cosine 相似度。
    Markdown 标题本身是结构边界：章节之间先切开，章节内部再在低于阈值处切开。
    如果直接对整篇文档统一算相似度，标题的向量往往比正文更“通用”，阈值低时
    完全不会触发，阈值高时又会把标题孤立成无信息 Chunk；按章节内判断可以避免
    这两个极端，同时保留“低相似度才切”的原则。

    随后仍受 ``max_chars`` 约束，避免单个语义段过长。这是可解释的切分，不是
    黑盒 LLM 切分。
    """

    embedding_client: EmbeddingClient
    max_chars: int = 800
    overlap_chars: int = 0
    threshold: float = 0.55
    window: int = 2
    name: str = "semantic"

    def split(self, text: str) -> list[ChunkDraft]:
        units = _split_units(text)
        if not units:
            return []
        if len(units) == 1:
            return FixedChunker(
                max_chars=self.max_chars, overlap_chars=self.overlap_chars
            ).split(text)

        sections: list[list[tuple[str, int, int]]] = []
        current_section: list[tuple[str, int, int]] = []
        for unit in units:
            is_heading = unit[0].lstrip().startswith("#")
            if is_heading and current_section:
                sections.append(current_section)
                current_section = []
            current_section.append(unit)
        if current_section:
            sections.append(current_section)

        groups: list[list[tuple[str, int, int]]] = []
        for section in sections:
            # 标题作为章节锚点，与本节正文一起参与检索；不单独发 Embedding。
            if len(section) <= 1:
                groups.append(section)
                continue
            body = section[1:]
            windows = [
                " ".join(item[0] for item in body[index : index + self.window])
                for index in range(len(body))
            ]
            vectors = self.embedding_client.embed_texts(windows)
            boundaries: set[int] = set()
            for index in range(1, len(body)):
                if _cosine(vectors[index - 1], vectors[index]) < self.threshold:
                    boundaries.add(index + 1)

            current = [section[0]]
            for index, unit in enumerate(body, start=1):
                if index in boundaries and current:
                    groups.append(current)
                    current = []
                current.append(unit)
            if current:
                groups.append(current)

        # 顶层标题后直接跟子标题时（``# 文档`` → ``## 章节``），父标题没有
        # 直属正文。把它并入下一组，避免产生只有标题、几乎没有检索价值的 Chunk。
        merged: list[list[tuple[str, int, int]]] = []
        pending_heading: tuple[str, int, int] | None = None
        for group in groups:
            if len(group) == 1 and group[0][0].lstrip().startswith("#"):
                pending_heading = group[0]
                continue
            if pending_heading is not None:
                group = [pending_heading, *group]
                pending_heading = None
            merged.append(group)
        if pending_heading is not None:
            merged.append([pending_heading])

        drafts: list[ChunkDraft] = []
        for group in merged:
            group_text = "\n".join(item[0] for item in group)
            if len(group_text) <= self.max_chars:
                drafts.append(
                    ChunkDraft(
                        content=group_text.strip(),
                        start=group[0][1],
                        end=group[-1][2],
                    )
                )
            else:
                drafts.extend(
                    _pack_units(
                        group,
                        max_chars=self.max_chars,
                        overlap_chars=self.overlap_chars,
                    )
                )
        return drafts


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


__all__ = [
    "ChunkDraft",
    "DocumentChunker",
    "FixedChunker",
    "SemanticChunker",
]
