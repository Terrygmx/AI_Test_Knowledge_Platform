"""服务层统一出口。"""

from __future__ import annotations

from app.services.base import EntityService
from app.services.defect_service import DefectService
from app.services.document_service import DocumentService
from app.services.requirement_service import RequirementService
from app.services.search_service import UnifiedSearchService
from app.services.testcase_service import TestCaseService

__all__ = [
    "DefectService",
    "DocumentService",
    "EntityService",
    "RequirementService",
    "TestCaseService",
    "UnifiedSearchService",
]
