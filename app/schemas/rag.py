"""最小 RAG 请求 / 响应 Schema。"""

from __future__ import annotations

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

    @field_validator("question")
    @classmethod
    def _question_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question 不能为空白")
        return value.strip()


class Citation(BaseSchema):
    """回答引用的知识片段，业务编号是最终追溯锚点。"""

    source_type: SourceType
    source_id: str
    chunk_index: int = Field(ge=0)
    snippet: str


class RAGResponse(BaseSchema):
    """RAG 回答。``grounded=false`` 表示证据不足，而不是生成失败。"""

    question: str
    answer: str
    grounded: bool
    citations: list[Citation] = Field(default_factory=list)
    retrieved_count: int = Field(ge=0)


__all__ = ["Citation", "RAGRequest", "RAGResponse"]
