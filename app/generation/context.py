"""把检索结果组装成有预算、有编号、可追溯的上下文。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.models import KnowledgeChunk
from app.schemas.rag import Citation

#: 单个 Chunk 最多放入 Prompt 的字符数
MAX_CHUNK_CHARS = 2000
#: 全部证据最多放入 Prompt 的字符数
MAX_CONTEXT_CHARS = 8000


@dataclass(frozen=True)
class ContextBundle:
    """组装后的证据上下文及与编号一一对应的 Citation。"""

    text: str
    citations: list[Citation]


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[: max(limit - 1, 0)]}…"


def assemble_context(
    results: Sequence[tuple[KnowledgeChunk, float]],
    *,
    max_context_chars: int = MAX_CONTEXT_CHARS,
) -> ContextBundle:
    """按检索顺序组装 ``[1] [2] ...`` 证据块。

    上下文预算从第一个候选开始累计；超预算的候选不会获得引用编号，
    避免 Prompt 声称存在但实际未提供给模型的证据。
    """
    blocks: list[str] = []
    citations: list[Citation] = []
    used = 0

    for chunk, _distance in results:
        raw_content = chunk.content.strip()
        if not raw_content:
            continue
        content = _truncate(raw_content, MAX_CHUNK_CHARS)
        citation_number = len(citations) + 1
        block = (
            f"[{citation_number}] 来源：{chunk.source_type}:{chunk.source_id}"
            f"（chunk_index={chunk.chunk_index}）\n{content}"
        )
        if citations and used + len(block) > max_context_chars:
            break
        blocks.append(block)
        citations.append(
            Citation(
                source_type=chunk.source_type,
                source_id=chunk.source_id,
                chunk_index=chunk.chunk_index,
                snippet=raw_content,
            )
        )
        used += len(block)

    return ContextBundle(text="\n\n".join(blocks), citations=citations)


__all__ = ["ContextBundle", "MAX_CHUNK_CHARS", "MAX_CONTEXT_CHARS", "assemble_context"]
