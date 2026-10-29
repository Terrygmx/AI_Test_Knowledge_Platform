"""SDD 契约验收：api-spec.yaml 必须与运行中实现一致。"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.api.contract import ERROR_RESPONSES, build_contract_spec
from app.main import create_app

SPEC_PATH = Path("docs/sdd/api-spec.yaml")
SDD_DIR = Path("docs/sdd")


def test_sdd_documents_exist() -> None:
    for name in ("proposal.md", "design.md", "api-spec.yaml", "tasks.md"):
        path = SDD_DIR / name
        assert path.is_file(), f"缺少 SDD 文档：{path}"
        assert path.stat().st_size > 500, f"SDD 文档内容过少：{path}"


def test_api_spec_matches_running_application() -> None:
    saved = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
    current = build_contract_spec(create_app())

    assert saved == current, (
        "api-spec.yaml 与实现不一致；执行 "
        "uv run python scripts/export_openapi.py 重新导出"
    )


def test_every_operation_declares_error_envelope() -> None:
    spec = build_contract_spec(create_app())

    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            responses = operation["responses"]
            for status in ERROR_RESPONSES:
                assert status in responses, f"{method.upper()} {path} 缺少 {status} 错误响应"
                schema = responses[status]["content"]["application/json"]["schema"]
                assert schema["$ref"] == "#/components/schemas/ErrorEnvelope"


def test_contract_declares_response_conventions() -> None:
    spec = build_contract_spec(create_app())

    assert spec["x-contract-version"] == "1.0"
    assert spec["x-error-envelope"] == "#/components/schemas/ErrorEnvelope"
    assert set(spec["x-response-conventions"]) == {"success", "error", "request_id"}
    assert "ErrorEnvelope" in spec["components"]["schemas"]
