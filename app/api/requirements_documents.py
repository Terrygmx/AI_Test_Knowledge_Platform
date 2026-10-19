"""需求 / 文档 API（读多写少，先提供完整 CRUD 保证数据可入）。"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import PaginationDep, SessionDep
from app.models import Document, Requirement
from app.models.enums import Priority, RequirementStatus
from app.schemas.common import Page, enum_query
from app.schemas.document import DocumentCreate, DocumentRead, DocumentUpdate
from app.schemas.requirement import RequirementCreate, RequirementRead, RequirementUpdate
from app.services.document_service import DocumentService
from app.services.requirement_service import RequirementService

requirement_router = APIRouter(prefix="/requirements", tags=["requirements"])
document_router = APIRouter(prefix="/documents", tags=["documents"])


# ---------------------------------------------------------------- 需求


@requirement_router.post(
    "",
    response_model=RequirementRead,
    status_code=status.HTTP_201_CREATED,
    summary="新增需求",
)
def create_requirement(payload: RequirementCreate, session: SessionDep) -> Requirement:
    return RequirementService.create(session, data=payload.to_orm_dict())


@requirement_router.get("", response_model=Page[RequirementRead], summary="需求列表")
def list_requirements(
    pagination: PaginationDep,
    session: SessionDep,
    module: str | None = None,
    version: str | None = None,
    priority: enum_query(Priority) = None,
    status: enum_query(RequirementStatus) = None,
    q: str | None = None,
) -> Page[RequirementRead]:
    items, total = RequirementService.list(
        session,
        offset=pagination.offset,
        limit=pagination.limit,
        filters={
            "module": module,
            "version": version,
            "priority": priority.value if priority else None,
            "status": status.value if status else None,
        },
        q=q,
    )
    return Page[RequirementRead].build(
        items=items, total=total, limit=pagination.limit, offset=pagination.offset
    )


@requirement_router.get("/{req_id}", response_model=RequirementRead, summary="按 Req ID 查询")
def get_requirement(req_id: str, session: SessionDep) -> Requirement:
    return RequirementService.get_by_unique_or_404(session, req_id)


@requirement_router.patch("/{req_id}", response_model=RequirementRead, summary="局部更新需求")
def update_requirement(req_id: str, payload: RequirementUpdate, session: SessionDep) -> Requirement:
    requirement = RequirementService.get_by_unique_or_404(session, req_id)
    return RequirementService.update(
        session, requirement, data=payload.to_orm_dict(exclude_unset=True)
    )


@requirement_router.delete("/{req_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除需求")
def delete_requirement(req_id: str, session: SessionDep) -> None:
    requirement = RequirementService.get_by_unique_or_404(session, req_id)
    RequirementService.delete(session, requirement)


# ---------------------------------------------------------------- 文档


@document_router.post(
    "",
    response_model=DocumentRead,
    status_code=status.HTTP_201_CREATED,
    summary="登记文档元数据",
)
def create_document(payload: DocumentCreate, session: SessionDep) -> Document:
    return DocumentService.create(session, data=payload.to_orm_dict())


@document_router.get("", response_model=Page[DocumentRead], summary="文档列表")
def list_documents(
    pagination: PaginationDep,
    session: SessionDep,
    doc_type: str | None = None,
    version: str | None = None,
    q: str | None = None,
) -> Page[DocumentRead]:
    items, total = DocumentService.list(
        session,
        offset=pagination.offset,
        limit=pagination.limit,
        filters={"doc_type": doc_type, "version": version},
        q=q,
    )
    return Page[DocumentRead].build(
        items=items, total=total, limit=pagination.limit, offset=pagination.offset
    )


@document_router.get("/{doc_id}", response_model=DocumentRead, summary="按 Doc ID 查询")
def get_document(doc_id: str, session: SessionDep) -> Document:
    return DocumentService.get_by_unique_or_404(session, doc_id)


@document_router.patch("/{doc_id}", response_model=DocumentRead, summary="局部更新文档元数据")
def update_document(doc_id: str, payload: DocumentUpdate, session: SessionDep) -> Document:
    document = DocumentService.get_by_unique_or_404(session, doc_id)
    return DocumentService.update(session, document, data=payload.to_orm_dict(exclude_unset=True))


@document_router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除文档")
def delete_document(doc_id: str, session: SessionDep) -> None:
    document = DocumentService.get_by_unique_or_404(session, doc_id)
    DocumentService.delete(session, document)


__all__ = ["document_router", "requirement_router"]
