"""统一异常 → HTTP 响应映射。

约定：所有错误响应都是同一个信封 ``{"error": {code, message, request_id, detail}}``；
未处理异常只回 500 + 通用文案，细节进日志（不泄露内部实现）。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.context import get_request_id
from app.core.exceptions import DomainError
from app.core.logging import get_logger
from app.schemas.common import ErrorResponse

logger = get_logger(__name__)

#: Starlette 状态码 → 机器可读错误码
_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    415: "unsupported_media_type",
    422: "unprocessable_entity",
    429: "too_many_requests",
}


def _respond(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    detail: Any | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    payload = ErrorResponse(
        error={
            "code": code,
            "message": message,
            "request_id": get_request_id(request),
            "detail": detail,
        }
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常 handler。"""

    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return _respond(
            request,
            exc.http_status,
            exc.code,
            exc.message,
            exc.detail or None,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _respond(
            request,
            422,
            "validation_error",
            "请求参数校验失败",
            _jsonable_errors(exc),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "http_error")
        return _respond(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "未处理异常 request_id=%s path=%s", get_request_id(request), request.url.path
        )
        return _respond(request, 500, "internal_error", "服务器内部错误，请稍后重试")


def _jsonable_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    """把 Pydantic 错误转成可 JSON 序列化的结构（ctx 里可能带异常对象）。"""
    from pydantic_core import ValidationError  # noqa: F401  # 文档用途

    errors: list[dict[str, Any]] = []
    for error in exc.errors():
        item = dict(error)
        ctx = item.get("ctx")
        if isinstance(ctx, dict):
            item["ctx"] = {k: str(v) for k, v in ctx.items()}
        errors.append(item)
    return errors


__all__ = ["register_exception_handlers"]
