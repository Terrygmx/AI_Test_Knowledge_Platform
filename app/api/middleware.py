"""请求级中间件：request_id + 耗时统计 + 访问日志。

Badcase 分析依赖「一次请求串起所有阶段」的线索，因此每个响应都带
``X-Request-ID``（客户端可传入，便于把上层系统的日志和这里对起来）和
``X-Process-Time-Ms``。
"""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.context import set_request_id
from app.core.logging import get_logger

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
PROCESS_TIME_HEADER = "X-Process-Time-Ms"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """注入 request_id 并记录访问日志。"""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:16]
        set_request_id(request, request_id)

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # 未处理异常交给全局 handler，这里只补一条日志
            logger.exception(
                "请求异常 %s %s request_id=%s", request.method, request.url.path, request_id
            )
            raise

        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers[PROCESS_TIME_HEADER] = f"{elapsed_ms:.1f}"
        logger.info(
            "%s %s -> %s %.1fms request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            request_id,
        )
        return response


__all__ = ["PROCESS_TIME_HEADER", "REQUEST_ID_HEADER", "RequestContextMiddleware"]
