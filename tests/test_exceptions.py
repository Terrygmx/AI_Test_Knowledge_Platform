"""领域异常测试。

约定：业务错误只抛 ``DomainError`` 子类，HTTP 映射统一在 ``app/api/errors.py``
的 handler 里完成，路由函数不自己拼错误响应。
"""

from __future__ import annotations

from app.core.exceptions import DomainError, DuplicateError, NotFoundError


class TestDomainExceptions:
    def test_domain_error_defaults(self) -> None:
        exc = DomainError("出了点问题")
        assert exc.code == "domain_error"
        assert exc.http_status == 400
        assert exc.message == "出了点问题"
        assert exc.detail == {}
        assert str(exc) == "出了点问题"

    def test_not_found_maps_to_404(self) -> None:
        exc = NotFoundError("测试用例 LOGIN-999 不存在")
        assert exc.code == "not_found"
        assert exc.http_status == 404

    def test_duplicate_maps_to_409(self) -> None:
        exc = DuplicateError("测试用例 LOGIN-001 已存在")
        assert exc.code == "duplicate"
        assert exc.http_status == 409

    def test_detail_is_optional_and_isolated(self) -> None:
        first = NotFoundError("a", detail={"field": "case_id"})
        second = NotFoundError("b")
        assert first.detail == {"field": "case_id"}
        assert second.detail == {}

    def test_hierarchy(self) -> None:
        for cls in (NotFoundError, DuplicateError):
            assert issubclass(cls, DomainError)
            assert issubclass(cls, Exception)

    def test_raise_and_catch_as_domain_error(self) -> None:
        import pytest

        with pytest.raises(DomainError) as exc_info:
            raise NotFoundError("x")
        assert exc_info.value.http_status == 404
