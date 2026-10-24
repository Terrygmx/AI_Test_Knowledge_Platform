"""最小 RAG 请求 / 响应 Schema。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from app.schemas.common import BaseSchema, SourceType


class RAGRequest(BaseSchema):
    """自然语言问题。"""

    question: str = Field(min_length=1, max_length=2000, description="测试知识问题")
    top_k: int = Field(default=5, ge=1, le=20, description="最多使用的证据片段数")
    source_types: list[SourceType] | None = Field(
        default=None,
        description="限定证据来源；不传则检索全部来源",
    )
    max_distance: float | None = Field(
        default=None,
        ge=0,
        le=2,
        description="可选 cosine distance 上限；超过则拒答，默认不设阈值",
    )

    @field_validator("question")
    @classmethod
    def _question_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question 不能为空白")
        return value.strip()


class Citation(BaseSchema):
    """回答引用的知识片段，业务编号是最终追溯锚点。"""

    chunk_id: int | None = Field(
        default=None,
        ge=1,
        description="knowledge_chunks 主键；离线实验没有数据库行时可为空",
    )
    source_type: SourceType
    source_id: str
    chunk_index: int = Field(ge=0)
    snippet: str
    rank: int = Field(ge=1, description="本次检索排序，从 1 开始")
    distance: float | None = Field(
        default=None, description="cosine distance；越小越相似，仅 PostgreSQL 检索可用"
    )
    retriever: Literal["vector", "keyword", "hybrid", "rerank"] = Field(
        default="vector", description="产生该证据的检索器"
    )
    score: float | None = Field(
        default=None,
        description="检索器原始分数；vector 使用 cosine distance，BM25 使用相关度分数",
    )


class RAGResponse(BaseSchema):
    """RAG 回答。

    ``citation_valid`` 只表示引用编号合法且回答至少引用一条证据，不代表 Faithfulness；
    ``evidence_sufficient`` 在配置 ``max_distance`` 时表示是否存在通过阈值的证据；
    未配置时为 ``None``，表示尚未建立相关性判定，而不是“无据”或“有据”。
    该字段与 LLM 是否引用证据无关；回答可追溯性由 ``citation_valid`` 表示。
    """

    question: str
    answer: str
    citation_valid: bool
    evidence_sufficient: bool | None = None
    citations: list[Citation] = Field(default_factory=list)
    retrieved_count: int = Field(ge=0)
    trace: dict[str, Any] | None = Field(
        default=None,
        description="Phase 18 逐阶段 pipeline trace；只在 pipeline 路径填充",
    )

    def source_lines(self) -> list[str]:
        """按引用顺序输出去重后的来源清单，用于展示“来源”区块。"""
        lines: list[str] = []
        seen: set[tuple[str, str]] = set()
        for citation in self.citations:
            key = (citation.source_type, citation.source_id)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"{citation.source_type}:{citation.source_id}")
        return lines


__all__ = ["Citation", "RAGRequest", "RAGResponse"]
