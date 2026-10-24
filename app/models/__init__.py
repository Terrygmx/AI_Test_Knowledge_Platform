"""ORM 实体统一出口。

导入本包即把所有表注册到 ``Base.metadata``（Alembic autogenerate 与测试建表都依赖这一点）。
"""

from __future__ import annotations

from app.models.badcase import EvaluationBadcase
from app.models.defect import Defect
from app.models.document import Document
from app.models.enums import (
    CaseType,
    DefectStatus,
    Priority,
    RequirementStatus,
    Severity,
)
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.requirement import Requirement
from app.models.test_case import TestCase
from app.models.types import JSONMap, TextArray

__all__ = [
    "CaseType",
    "Defect",
    "DefectStatus",
    "Document",
    "EvaluationBadcase",
    "JSONMap",
    "KnowledgeChunk",
    "Priority",
    "Requirement",
    "RequirementStatus",
    "Severity",
    "TestCase",
    "TextArray",
]
