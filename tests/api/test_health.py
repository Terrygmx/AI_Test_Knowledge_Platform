"""健康检查 API 测试。"""

from __future__ import annotations

API = "/api/v1"


class TestHealth:
    def test_health_ok(self, client, monkeypatch) -> None:
        from app.api import health

        monkeypatch.setattr(health, "_check_llm", lambda: True)
        monkeypatch.setattr(health, "_check_embedding", lambda: True)
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["checks"]["database"] is True
        assert body["metrics"]["chunks_without_embedding"] == 0
        assert body["app"]
        assert body["version"]

    def test_capabilities_reported(self, client, engine, monkeypatch) -> None:
        """让使用者一眼看出 Embedding / Rerank / LLM 是否可用。"""
        from app.api import health

        monkeypatch.setattr(health, "_check_llm", lambda: True)
        monkeypatch.setattr(health, "_check_embedding", lambda: True)
        body = client.get("/health").json()
        caps = body["capabilities"]
        assert set(caps) == {"embedding", "rerank", "llm", "vector_search"}
        assert all(isinstance(v, bool) for v in caps.values())
        assert caps["vector_search"] is (engine.dialect.name == "postgresql")

    def test_health_does_not_need_auth_or_body(self, client, monkeypatch) -> None:
        from app.api import health

        monkeypatch.setattr(health, "_check_llm", lambda: True)
        monkeypatch.setattr(health, "_check_embedding", lambda: True)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() is not None

    def test_llm_probe_uses_cache(self, monkeypatch) -> None:
        from app.api import health

        calls = 0

        def available(*args, **kwargs):
            nonlocal calls
            calls += 1
            return True

        monkeypatch.setattr(health, "_llm_cache", None)
        monkeypatch.setattr(health.settings, "llm_provider", "openai_compatible")
        monkeypatch.setattr(health.settings, "llm_api_key", "test-key")
        monkeypatch.setattr(health, "_model_available", available)
        assert health._check_llm() is True
        assert health._check_llm() is True
        assert calls == 1

    def test_llm_probe_failure_is_false(self, monkeypatch) -> None:
        from app.api import health

        class FailingClient:
            def get(self, *args, **kwargs):
                raise health.httpx.ConnectError("offline")

        monkeypatch.setattr(health, "_llm_cache", None)
        monkeypatch.setattr(health.settings, "llm_provider", "ollama")
        monkeypatch.setattr(health.httpx, "get", FailingClient().get)
        assert health._check_llm() is False

    def test_llm_probe_refreshes_after_ttl(self, monkeypatch) -> None:
        from app.api import health

        monkeypatch.setattr(health.settings, "llm_provider", "openai_compatible")
        monkeypatch.setattr(health.settings, "llm_api_key", "")
        monkeypatch.setattr(health, "_llm_cache", (0.0, True))
        monkeypatch.setattr(health.time, "monotonic", lambda: 61.0)
        assert health._check_llm() is False

    def test_llm_probe_catches_non_http_exceptions(self, monkeypatch) -> None:
        from app.api import health

        def broken(*args, **kwargs):
            raise TimeoutError("timeout")

        monkeypatch.setattr(health, "_llm_cache", None)
        monkeypatch.setattr(health.settings, "llm_provider", "ollama")
        monkeypatch.setattr(health.httpx, "get", broken)
        assert health._check_llm() is False

    def test_openai_compatible_dead_endpoint_is_false(self, monkeypatch) -> None:
        from app.api import health

        def dead(*args, **kwargs):
            raise health.httpx.ConnectError("offline")

        monkeypatch.setattr(health, "_llm_cache", None)
        monkeypatch.setattr(health.settings, "llm_provider", "openai_compatible")
        monkeypatch.setattr(health.settings, "llm_api_key", "omlx")
        monkeypatch.setattr(health.settings, "llm_base_url", "http://127.0.0.1:9999/v1")
        monkeypatch.setattr(health.httpx, "get", dead)
        assert health._check_llm() is False

    def test_openai_compatible_missing_model_is_false(self, monkeypatch) -> None:
        from app.api import health

        class Response:
            status_code = 200

            def json(self):
                return {"data": [{"id": "another-model"}]}

        monkeypatch.setattr(health, "_llm_cache", None)
        monkeypatch.setattr(health.settings, "llm_provider", "openai_compatible")
        monkeypatch.setattr(health.settings, "llm_model", "missing-model")
        monkeypatch.setattr(health.httpx, "get", lambda *args, **kwargs: Response())
        assert health._check_llm() is False

    def test_embedding_probe_missing_model_is_false(self, monkeypatch) -> None:
        from app.api import health

        class Response:
            status_code = 200

            def json(self):
                return {"data": [{"id": "another-model"}]}

        monkeypatch.setattr(health, "_embedding_cache", None)
        monkeypatch.setattr(health.settings, "embedding_model", "missing-model")
        monkeypatch.setattr(health.httpx, "get", lambda *args, **kwargs: Response())
        assert health._check_embedding() is False
