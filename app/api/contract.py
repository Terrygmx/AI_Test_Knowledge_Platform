"""SDD API 契约：在 FastAPI 自动 OpenAPI 之上补齐统一错误信封。

FastAPI 只能看到成功响应模型；本项目的错误响应由全局异常处理器生成，
因此不会自动出现在 OpenAPI 里。这里把错误信封显式声明到每个操作上，
让 `docs/sdd/api-spec.yaml` 成为可验收的接口契约，而不是只有 200 的文档。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from fastapi import FastAPI

ERROR_ENVELOPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["error"],
    "properties": {
        "error": {
            "type": "object",
            "required": ["code", "message", "request_id", "detail"],
            "properties": {
                "code": {
                    "type": "string",
                    "description": "机器可读错误码，如 not_found / duplicate / validation_error",
                },
                "message": {"type": "string", "description": "人类可读错误信息"},
                "request_id": {
                    "type": ["string", "null"],
                    "description": "请求追踪 ID（X-Request-ID）",
                },
                "detail": {
                    "description": "字段级细节；不同错误码结构不同",
                },
            },
        }
    },
}

#: 所有端点共享的错误响应。描述同时说明该状态码下的典型 code。
ERROR_RESPONSES: dict[str, dict[str, Any]] = {
    "400": {"description": "业务校验失败（domain_error / bad_request）"},
    "404": {"description": "资源不存在（not_found）"},
    "409": {"description": "唯一约束冲突（duplicate / conflict）"},
    "422": {"description": "请求参数校验失败（validation_error）"},
    "429": {"description": "并发闸门满载（rag_overloaded / too_many_requests）"},
    "500": {"description": "未处理异常（internal_error，不回显内部细节）"},
    "503": {"description": "上游模型或 Embedding 不可用 / 超时（llm_error / embedding_error）"},
}


def build_contract_spec(app: FastAPI) -> dict[str, Any]:
    """生成带错误信封的完整 OpenAPI 契约（深拷贝，不污染应用缓存）。"""
    spec = deepcopy(app.openapi())
    spec.setdefault("components", {}).setdefault("schemas", {})["ErrorEnvelope"] = (
        deepcopy(ERROR_ENVELOPE_SCHEMA)
    )
    spec["x-contract-version"] = "1.0"
    spec["x-error-envelope"] = "#/components/schemas/ErrorEnvelope"
    spec["x-response-conventions"] = {
        "success": "2xx 返回具体资源模型或 Page 包装",
        "error": "所有非 2xx 返回统一错误信封",
        "request_id": "响应头 X-Request-ID 与错误体 request_id 一致",
    }
    for operations in spec.get("paths", {}).values():
        for operation in operations.values():
            if not isinstance(operation, dict) or "responses" not in operation:
                continue
            for status, meta in ERROR_RESPONSES.items():
                operation["responses"][status] = {
                    "description": meta["description"],
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/ErrorEnvelope"}
                        }
                    },
                }
    return spec


__all__ = ["ERROR_ENVELOPE_SCHEMA", "ERROR_RESPONSES", "build_contract_spec"]
