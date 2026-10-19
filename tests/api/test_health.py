"""健康检查 API 测试。"""

from __future__ import annotations

API = "/api/v1"


class TestHealth:
    def test_health_ok(self, client) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["checks"]["database"] is True
        assert body["app"]
        assert body["version"]

    def test_capabilities_reported(self, client, engine) -> None:
        """让使用者一眼看出 Embedding / Rerank / LLM 是否可用。"""
        body = client.get("/health").json()
        caps = body["capabilities"]
        assert set(caps) == {"embedding", "rerank", "llm", "vector_search"}
        assert all(isinstance(v, bool) for v in caps.values())
        assert caps["vector_search"] is (engine.dialect.name == "postgresql")

    def test_health_does_not_need_auth_or_body(self, client) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() is not None
