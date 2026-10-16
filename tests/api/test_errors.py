"""错误处理与中间件测试。"""

from __future__ import annotations

from fastapi import Request


class TestErrorEnvelope:
    def test_unknown_route_returns_envelope(self, client) -> None:
        resp = client.get("/api/v1/does-not-exist")
        assert resp.status_code == 404
        body = resp.json()
        assert body["error"]["code"] == "not_found"
        assert "request_id" in body["error"]

    def test_wrong_method_returns_envelope(self, client) -> None:
        resp = client.put("/health")
        assert resp.status_code == 405
        assert resp.json()["error"]["code"] == "method_not_allowed"

    def test_validation_error_shape(self, client) -> None:
        resp = client.post("/api/v1/testcases", json={"case_id": "BAD ID"})
        assert resp.status_code == 422
        error = resp.json()["error"]
        assert error["code"] == "validation_error"
        assert error["detail"]  # 字段级明细
        assert error["request_id"]

    def test_not_found_carries_request_id(self, client) -> None:
        resp = client.get("/api/v1/testcases/NOPE-1")
        assert resp.json()["error"]["request_id"]

    def test_unhandled_exception_is_masked(self, client, app) -> None:
        """内部异常不能把堆栈 / 细节泄露给调用方。"""

        @app.get("/boom")
        def boom() -> None:
            raise RuntimeError("secret internal detail")

        resp = client.get("/boom")
        assert resp.status_code == 500
        body = resp.json()
        assert body["error"]["code"] == "internal_error"
        assert "secret internal detail" not in resp.text
        assert body["error"]["request_id"]


class TestRequestContextMiddleware:
    def test_generates_request_id(self, client) -> None:
        resp = client.get("/health")
        assert resp.headers["X-Request-ID"]

    def test_echoes_client_request_id(self, client) -> None:
        resp = client.get("/health", headers={"X-Request-ID": "trace-123"})
        assert resp.headers["X-Request-ID"] == "trace-123"

    def test_reports_process_time(self, client) -> None:
        resp = client.get("/health")
        assert float(resp.headers["X-Process-Time-Ms"]) >= 0

    def test_request_id_is_available_on_request_state(self, client, app) -> None:
        seen: dict[str, str] = {}

        @app.get("/echo-request-id")
        def echo(request: Request) -> dict[str, str]:
            seen["id"] = request.state.request_id
            return {"ok": "yes"}

        client.get("/echo-request-id", headers={"X-Request-ID": "abc"})
        assert seen["id"] == "abc"


class TestAppMetadata:
    def test_openapi_document(self, client) -> None:
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        paths = resp.json()["paths"]
        assert "/health" in paths
        assert "/api/v1/testcases" in paths
        assert "/api/v1/search" in paths

    def test_docs_page(self, client) -> None:
        assert client.get("/docs").status_code == 200

    def test_title_and_version(self, app) -> None:
        assert app.title
        assert app.version
