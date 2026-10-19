"""Phase 6 实验数据集契约。

Gold Set 的粒度是**来源实体 + 原文证据片段**，不是 Chunk ID。切分策略变化会
改变 Chunk 编号，如果标注绑定 Chunk，实验就失去可比性。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from app.schemas.common import BaseSchema, SourceType


class EvidenceLabel(BaseSchema):
    """一条人工确认的相关证据；``evidence`` 用于策略无关的归属匹配。"""

    source_type: SourceType
    source_id: str = Field(min_length=1)
    evidence: str = Field(min_length=1)


class EvaluationQuery(BaseSchema):
    query_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    relevant: list[EvidenceLabel] = Field(default_factory=list)
    answerable: bool = True
    category: str = ""

    @model_validator(mode="after")
    def _validate_answerability(self) -> EvaluationQuery:
        if self.answerable and not self.relevant:
            raise ValueError("可回答问题必须至少标注一条相关证据")
        if not self.answerable and self.relevant:
            raise ValueError("不可回答问题不应标注相关证据")
        return self


class TestCaseSeed(BaseSchema):
    case_id: str
    title: str
    module: str | None = None
    version: str | None = None
    priority: str | None = None
    case_type: str | None = None
    precondition: str | None = None
    steps: str | None = None
    expected_result: str | None = None
    tags: list[str] = Field(default_factory=list)


class DefectSeed(BaseSchema):
    bug_id: str
    title: str
    module: str | None = None
    version: str | None = None
    severity: str | None = None
    priority: str | None = None
    status: str | None = None
    reproduce_steps: str | None = None
    root_cause: str | None = None
    resolution: str | None = None
    related_cases: list[str] = Field(default_factory=list)


class RequirementSeed(BaseSchema):
    req_id: str
    title: str
    module: str | None = None
    version: str | None = None
    priority: str | None = None
    status: str | None = None
    content: str | None = None


class DocumentSeed(BaseSchema):
    doc_id: str
    title: str
    doc_type: str = "markdown"
    version: str | None = None
    path: str | None = None
    content: str = Field(min_length=1)


class SourceReference(BaseSchema):
    name: str
    url: str
    usage: str


class ChunkingDataset(BaseSchema):
    version: str
    description: str
    sources: list[SourceReference] = Field(default_factory=list)
    testcases: list[TestCaseSeed] = Field(default_factory=list)
    defects: list[DefectSeed] = Field(default_factory=list)
    requirements: list[RequirementSeed] = Field(default_factory=list)
    documents: list[DocumentSeed] = Field(default_factory=list)
    queries: list[EvaluationQuery]

    @model_validator(mode="after")
    def _validate_references(self) -> ChunkingDataset:
        ids: dict[str, set[str]] = {
            "testcase": {item.case_id for item in self.testcases},
            "defect": {item.bug_id for item in self.defects},
            "requirement": {item.req_id for item in self.requirements},
            "document": {item.doc_id for item in self.documents},
        }
        query_ids: set[str] = set()
        for query in self.queries:
            if query.query_id in query_ids:
                raise ValueError(f"query_id 重复：{query.query_id}")
            query_ids.add(query.query_id)
            for label in query.relevant:
                if label.source_id not in ids[label.source_type]:
                    raise ValueError(
                        f"{query.query_id} 引用了不存在的来源："
                        f"{label.source_type}:{label.source_id}"
                    )
        return self

    @property
    def dataset_sha256(self) -> str:
        payload = self.model_dump(mode="json")
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_dataset(path: str | Path) -> ChunkingDataset:
    """读取 JSON 数据集；解析错误直接抛出，不做静默修正。"""
    raw: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    return ChunkingDataset.model_validate(raw)


__all__ = [
    "ChunkingDataset",
    "DefectSeed",
    "DocumentSeed",
    "EvaluationQuery",
    "EvidenceLabel",
    "RequirementSeed",
    "SourceReference",
    "TestCaseSeed",
    "load_dataset",
]
