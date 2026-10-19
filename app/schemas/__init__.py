"""API Schema 统一出口。"""

from __future__ import annotations

from app.schemas.common import (
    BUSINESS_ID_PATTERN,
    BaseSchema,
    CaseTypeField,
    DefectStatusField,
    ErrorBody,
    ErrorResponse,
    ORMInput,
    Page,
    Pagination,
    PriorityField,
    RequirementStatusField,
    SeverityField,
    normalize_enum,
)
from app.schemas.defect import DefectCreate, DefectRead, DefectUpdate
from app.schemas.document import DocumentCreate, DocumentRead, DocumentUpdate
from app.schemas.rag import Citation, RAGRequest, RAGResponse
from app.schemas.requirement import RequirementCreate, RequirementRead, RequirementUpdate
from app.schemas.search import SearchHit, SearchResponse, SourceType
from app.schemas.test_case import TestCaseCreate, TestCaseRead, TestCaseUpdate

__all__ = [
    "BUSINESS_ID_PATTERN",
    "BaseSchema",
    "CaseTypeField",
    "Citation",
    "DefectCreate",
    "DefectRead",
    "DefectStatusField",
    "DefectUpdate",
    "DocumentCreate",
    "DocumentRead",
    "DocumentUpdate",
    "ErrorBody",
    "ErrorResponse",
    "ORMInput",
    "Page",
    "Pagination",
    "PriorityField",
    "RAGRequest",
    "RAGResponse",
    "RequirementCreate",
    "RequirementRead",
    "RequirementStatusField",
    "RequirementUpdate",
    "SearchHit",
    "SearchResponse",
    "SeverityField",
    "SourceType",
    "TestCaseCreate",
    "TestCaseRead",
    "TestCaseUpdate",
    "normalize_enum",
]
