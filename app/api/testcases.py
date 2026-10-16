"""测试用例 API。

公开接口统一用**业务编号**（Case ID）寻址，而不是自增主键：
用户引用、检索、引用出处（Citation）用的都是 Case ID。
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import PaginationDep, SessionDep
from app.models import TestCase
from app.models.enums import CaseType, Priority
from app.schemas.common import Page
from app.schemas.test_case import TestCaseCreate, TestCaseRead, TestCaseUpdate
from app.services.testcase_service import TestCaseService

router = APIRouter(prefix="/testcases", tags=["testcases"])


@router.post(
    "",
    response_model=TestCaseRead,
    status_code=status.HTTP_201_CREATED,
    summary="新增测试用例",
)
def create_testcase(payload: TestCaseCreate, session: SessionDep) -> TestCase:
    return TestCaseService.create(session, data=payload.to_orm_dict())


@router.get("", response_model=Page[TestCaseRead], summary="用例列表（元数据过滤 + 关键词）")
def list_testcases(
    pagination: PaginationDep,
    session: SessionDep,
    module: str | None = None,
    sub_module: str | None = None,
    version: str | None = None,
    priority: Priority | None = None,
    case_type: CaseType | None = None,
    q: str | None = None,
) -> Page[TestCaseRead]:
    items, total = TestCaseService.list(
        session,
        offset=pagination.offset,
        limit=pagination.limit,
        filters={
            "module": module,
            "sub_module": sub_module,
            "version": version,
            "priority": priority.value if priority else None,
            "case_type": case_type.value if case_type else None,
        },
        q=q,
    )
    return Page[TestCaseRead].build(
        items=items, total=total, limit=pagination.limit, offset=pagination.offset
    )


@router.get("/{case_id}", response_model=TestCaseRead, summary="按 Case ID 查询")
def get_testcase(case_id: str, session: SessionDep) -> TestCase:
    return TestCaseService.get_by_unique_or_404(session, case_id)


@router.patch("/{case_id}", response_model=TestCaseRead, summary="局部更新用例")
def update_testcase(case_id: str, payload: TestCaseUpdate, session: SessionDep) -> TestCase:
    case = TestCaseService.get_by_unique_or_404(session, case_id)
    return TestCaseService.update(session, case, data=payload.to_orm_dict(exclude_unset=True))


@router.delete("/{case_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除用例")
def delete_testcase(case_id: str, session: SessionDep) -> None:
    case = TestCaseService.get_by_unique_or_404(session, case_id)
    TestCaseService.delete(session, case)


__all__ = ["router"]
