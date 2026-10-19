"""领域异常。

约定：
- 业务错误一律抛 ``DomainError`` 子类，由 ``app/api/errors.py`` 注册的 handler
  统一转成 HTTP 响应；路由函数不自己拼 error dict；
- 异常同时携带 ``code``（机器可读）与 ``message``（人类可读），
  ``detail`` 放字段级细节（如哪个字段冲突）。
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """业务错误基类。"""

    code: str = "domain_error"
    http_status: int = 400

    def __init__(
        self,
        message: str,
        *,
        detail: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail: dict[str, Any] = detail or {}
        self.headers = headers or {}


class NotFoundError(DomainError):
    """资源不存在 → 404。"""

    code = "not_found"
    http_status = 404


class DuplicateError(DomainError):
    """唯一约束冲突（业务编号重复）→ 409。"""

    code = "duplicate"
    http_status = 409


__all__ = ["DomainError", "DuplicateError", "NotFoundError"]
