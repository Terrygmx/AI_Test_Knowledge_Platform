"""KnowledgeChunk API 测试。"""

from __future__ import annotations

API = "/api/v1/chunks"


class TestChunkAPI:
    def test_create_chunk(self, client) -> None:
        response = client.post(
            API,
            json={
                "source_type": "testcase",
                "source_id": "LOGIN-001",
                "content": "登录成功",
                "metadata": {"module": "login"},
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["source_type"] == "testcase"
        assert body["metadata"] == {"module": "login"}
        assert "meta" not in body
        assert len(body["content_hash"]) == 64
        assert "embedding" not in body

    def test_blank_content_422(self, client) -> None:
        response = client.post(
            API,
            json={
                "source_type": "testcase",
                "source_id": "LOGIN-002",
                "content": "   ",
            },
        )
        assert response.status_code == 422

    def test_invalid_source_type_422(self, client) -> None:
        response = client.post(
            API,
            json={
                "source_type": "unknown",
                "source_id": "X-1",
                "content": "x",
            },
        )
        assert response.status_code == 422
