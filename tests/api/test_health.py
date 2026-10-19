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

    def test_llm_probe_uses_cache(self, monkeypatch) -> None:
        from app.api import health

        monkeypatch.setattr(health, "_llm_cache", None)
        monkeypatch.setattr(health.settings, "llm_provider", "openai_compatible")
        monkeypatch.setattr(health.settings, "llm_api_key", "test-key")
        assert health._check_llm() is True
        assert health._check_llm() is True

    def test_llm_probe_failure_is_false(self, monkeypatch) -> None:
        from app.api import health

        class FailingClient:
            def get(self, *args, **kwargs):
                raise health.httpx.ConnectError("offline")

        monkeypatch.setattr(health, "_llm_cache", None)
        monkeypatch.setattr(health.settings, "llm_provider", "ollama")
        monkeypatch.setattr(health.httpx, "get", FailingClient().get)
        assert health._check_llm() is False
