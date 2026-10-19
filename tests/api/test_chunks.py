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

    def test_duplicate_chunk_409(self, client) -> None:
        payload = {
            "source_type": "testcase",
            "source_id": "LOGIN-409",
            "content": "重复内容",
        }
        assert client.post(API, json=payload).status_code == 201
        response = client.post(API, json=payload)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "duplicate"

    def test_list_and_delete_chunk(self, client) -> None:
        created = client.post(
            API,
            json={
                "source_type": "defect",
                "source_id": "BUG-LIST-1",
                "content": "支付失败",
            },
        ).json()
        listed = client.get(API, params={"source_type": "defect", "source_id": "BUG-LIST-1"})
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["id"] == created["id"]

        deleted = client.delete(f"{API}/{created['id']}")
        assert deleted.status_code == 204
        assert client.get(API, params={"source_id": "BUG-LIST-1"}).json()["total"] == 0

    def test_delete_missing_chunk_404(self, client) -> None:
        response = client.delete(f"{API}/999999")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
