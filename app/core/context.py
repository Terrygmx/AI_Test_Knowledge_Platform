"""请求上下文：把 request_id 挂在 ``request.state`` 上。

用 ``request.state`` 而不是 ContextVar：``BaseHTTPMiddleware`` 会把下游调用
放到独立任务里，ContextVar 不一定能回流到 handler。
"""

from __future__ import annotations

from fastapi import Request


def set_request_id(request: Request, request_id: str) -> None:
    request.state.request_id = request_id


def get_request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


__all__ = ["get_request_id", "set_request_id"]
