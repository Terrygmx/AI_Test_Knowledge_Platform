"""测试知识索引服务：从实体生成检索单元。

Phase 2 的职责是证明“原始实体 → Chunk → Embedding → pgvector”链路可运行。
选择一条实体一 Chunk：

- TestCase：标题、前置条件、步骤、预期结果、标签；
- Defect：标题、复现步骤、根因、解决方案；
- Requirement：标题、正文；
- Document：由调用方传入已切分正文（Phase 5 接入 Parser / Chunker）。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.models import Defect, Document, KnowledgeChunk, Requirement, TestCase
from app.retrieval.embedding import EmbeddingClient
from app.schemas.knowledge_chunk import KnowledgeChunkCreate
from app.services.knowledge_chunk_service import KnowledgeChunkService


def _join_non_empty(parts: Sequence[str | None]) -> str:
    return "\n".join(part.strip() for part in parts if part and part.strip())


def case_to_chunk(case: TestCase) -> KnowledgeChunkCreate:
    content = _join_non_empty(
        [
            f"用例编号：{case.case_id}",
            f"标题：{case.title}",
            f"前置条件：{case.precondition}" if case.precondition else None,
            f"步骤：{case.steps}" if case.steps else None,
            f"预期结果：{case.expected_result}" if case.expected_result else None,
            f"标签：{', '.join(case.tags)}" if case.tags else None,
        ]
    )
    return KnowledgeChunkCreate(
        source_type="testcase",
        source_id=case.case_id,
        chunk_index=0,
        content=content,
        metadata={
            "module": case.module,
            "sub_module": case.sub_module,
            "version": case.version,
            "priority": case.priority,
            "case_type": case.case_type,
            "tags": list(case.tags or []),
        },
        version=case.version,
    )


def defect_to_chunk(defect: Defect) -> KnowledgeChunkCreate:
    content = _join_non_empty(
        [
            f"缺陷编号：{defect.bug_id}",
            f"标题：{defect.title}",
            f"复现步骤：{defect.reproduce_steps}" if defect.reproduce_steps else None,
            f"根因：{defect.root_cause}" if defect.root_cause else None,
            f"解决方案：{defect.resolution}" if defect.resolution else None,
        ]
    )
    return KnowledgeChunkCreate(
        source_type="defect",
        source_id=defect.bug_id,
        chunk_index=0,
        content=content,
        metadata={
            "module": defect.module,
            "version": defect.version,
            "priority": defect.priority,
            "severity": defect.severity,
            "status": defect.status,
            "related_cases": list(defect.related_cases or []),
        },
        version=defect.version,
    )


def requirement_to_chunk(requirement: Requirement) -> KnowledgeChunkCreate:
    content = _join_non_empty(
        [
            f"需求编号：{requirement.req_id}",
            f"标题：{requirement.title}",
            requirement.content,
        ]
    )
    return KnowledgeChunkCreate(
        source_type="requirement",
        source_id=requirement.req_id,
        chunk_index=0,
        content=content,
        metadata={
            "module": requirement.module,
            "version": requirement.version,
            "priority": requirement.priority,
            "status": requirement.status,
        },
        version=requirement.version,
    )


def document_to_chunk(document: Document, *, content: str, chunk_index: int = 0) -> KnowledgeChunkCreate:
    return KnowledgeChunkCreate(
        source_type="document",
        source_id=document.doc_id,
        document_id=document.doc_id,
        chunk_index=chunk_index,
        content=content,
        metadata={"doc_type": document.doc_type, "version": document.version},
        version=document.version,
    )


class IndexService:
    """把业务实体索引为 KnowledgeChunk。"""

    @classmethod
    def index_entities(
        cls,
        session: Session,
        *,
        embedding_client: EmbeddingClient,
        testcases: Sequence[TestCase] = (),
        defects: Sequence[Defect] = (),
        requirements: Sequence[Requirement] = (),
        documents: Sequence[tuple[Document, str]] = (),
    ) -> list[KnowledgeChunk]:
        items: list[KnowledgeChunkCreate] = []
        items.extend(case_to_chunk(case) for case in testcases)
        items.extend(defect_to_chunk(defect) for defect in defects)
        items.extend(requirement_to_chunk(requirement) for requirement in requirements)
        for document, content in documents:
            items.append(document_to_chunk(document, content=content))

        if not items:
            return []
        return KnowledgeChunkService.create_many(
            session,
            items=items,
            embedding_client=embedding_client,
        )


__all__ = [
    "IndexService",
    "case_to_chunk",
    "defect_to_chunk",
    "document_to_chunk",
    "requirement_to_chunk",
]
