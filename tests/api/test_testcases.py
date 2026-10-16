"""测试用例 API 测试。"""

from __future__ import annotations

import pytest

API = "/api/v1/testcases"

PAYLOAD = {
    "case_id": "LOGIN-001",
    "title": "短信验证码登录冒烟",
    "module": "login",
    "version": "v1.3",
    "priority": "P0",
    "case_type": "smoke",
    "steps": "1. 输入手机号\n2. 获取验证码\n3. 点击登录",
    "expected_result": "登录成功并跳转首页",
    "tags": ["冒烟", "登录"],
    "source": "禅道导出",
}


class TestCreate:
    def test_create_returns_201_with_read_model(self, client) -> None:
        resp = client.post(API, json=PAYLOAD)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["case_id"] == "LOGIN-001"
        assert body["id"] > 0
        assert body["priority"] == "P0"
        assert body["tags"] == ["冒烟", "登录"]
        assert body["created_at"]

    def test_create_normalizes_priority_case(self, client) -> None:
        resp = client.post(API, json={**PAYLOAD, "priority": "p0"})
        assert resp.status_code == 201
        assert resp.json()["priority"] == "P0"

    def test_create_strips_whitespace(self, client) -> None:
        resp = client.post(API, json={**PAYLOAD, "case_id": "  LOGIN-002  ", "module": " login "})
        assert resp.status_code == 201
        assert resp.json()["case_id"] == "LOGIN-002"
        assert resp.json()["module"] == "login"

    def test_create_duplicate_returns_409(self, client) -> None:
        client.post(API, json=PAYLOAD)
        resp = client.post(API, json={**PAYLOAD, "title": "另一个标题"})
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "duplicate"
        assert "LOGIN-001" in resp.json()["error"]["message"]

    def test_create_invalid_case_id_returns_422(self, client) -> None:
        resp = client.post(API, json={**PAYLOAD, "case_id": "LOGIN 001"})
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "validation_error"

    def test_create_missing_title_returns_422(self, client) -> None:
        payload = {k: v for k, v in PAYLOAD.items() if k != "title"}
        resp = client.post(API, json=payload)
        assert resp.status_code == 422

    def test_create_unknown_priority_returns_422(self, client) -> None:
        resp = client.post(API, json={**PAYLOAD, "priority": "P9"})
        assert resp.status_code == 422


class TestList:
    @pytest.fixture()
    def seeded(self, client):
        rows = [
            {
                **PAYLOAD,
                "case_id": "LOGIN-001",
                "module": "login",
                "priority": "P0",
                "version": "v1.2",
            },
            {
                **PAYLOAD,
                "case_id": "LOGIN-002",
                "module": "login",
                "priority": "P1",
                "version": "v1.3",
            },
            {
                **PAYLOAD,
                "case_id": "PAY-001",
                "module": "payment",
                "priority": "P0",
                "version": "v1.3",
            },
        ]
        for row in rows:
            assert client.post(API, json=row).status_code == 201
        return rows

    def test_list_returns_page(self, client, seeded) -> None:
        resp = client.get(API)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 3
        assert body["limit"] == 20
        assert body["offset"] == 0
        assert [i["case_id"] for i in body["items"]] == ["LOGIN-001", "LOGIN-002", "PAY-001"]

    def test_list_pagination(self, client, seeded) -> None:
        body = client.get(API, params={"limit": 2, "offset": 0}).json()
        assert [i["case_id"] for i in body["items"]] == ["LOGIN-001", "LOGIN-002"]
        body = client.get(API, params={"limit": 2, "offset": 2}).json()
        assert [i["case_id"] for i in body["items"]] == ["PAY-001"]

    def test_list_filter_by_module(self, client, seeded) -> None:
        body = client.get(API, params={"module": "payment"}).json()
        assert body["total"] == 1
        assert body["items"][0]["case_id"] == "PAY-001"

    def test_list_filter_by_priority_and_version(self, client, seeded) -> None:
        body = client.get(API, params={"priority": "P0", "version": "v1.3"}).json()
        assert body["total"] == 1
        assert body["items"][0]["case_id"] == "PAY-001"

    def test_list_invalid_limit_returns_422(self, client, seeded) -> None:
        assert client.get(API, params={"limit": 0}).status_code == 422
        assert client.get(API, params={"limit": 1000}).status_code == 422

    def test_list_invalid_priority_returns_422(self, client, seeded) -> None:
        resp = client.get(API, params={"priority": "P9"})
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "validation_error"

    def test_list_keyword(self, client, seeded) -> None:
        body = client.get(API, params={"q": "支付"}).json()
        assert body["total"] == 0
        body = client.get(API, params={"q": "登录"}).json()
        assert body["total"] == 3

    def test_list_empty(self, client) -> None:
        body = client.get(API).json()
        assert body["items"] == []
        assert body["total"] == 0


class TestGet:
    def test_get_by_case_id(self, client) -> None:
        client.post(API, json=PAYLOAD)
        resp = client.get(f"{API}/LOGIN-001")
        assert resp.status_code == 200
        assert resp.json()["case_id"] == "LOGIN-001"

    def test_get_unknown_returns_404(self, client) -> None:
        resp = client.get(f"{API}/NOPE-1")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"


class TestUpdate:
    def test_patch_partial(self, client) -> None:
        client.post(API, json=PAYLOAD)
        resp = client.patch(f"{API}/LOGIN-001", json={"priority": "P1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["priority"] == "P1"
        assert body["title"] == PAYLOAD["title"]

    def test_patch_unknown_returns_404(self, client) -> None:
        resp = client.patch(f"{API}/NOPE-1", json={"priority": "P1"})
        assert resp.status_code == 404

    def test_patch_cannot_change_case_id(self, client) -> None:
        """Case ID 是引用锚点，不可变：传了必须报错而不是被静默忽略。"""
        client.post(API, json=PAYLOAD)
        client.post(API, json={**PAYLOAD, "case_id": "LOGIN-002"})
        resp = client.patch(f"{API}/LOGIN-001", json={"case_id": "LOGIN-002"})
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "validation_error"
        # 原记录未被改动
        assert client.get(f"{API}/LOGIN-001").json()["case_id"] == "LOGIN-001"

    def test_patch_rejects_unknown_field(self, client) -> None:
        client.post(API, json=PAYLOAD)
        resp = client.patch(f"{API}/LOGIN-001", json={"titl": "笔误"})
        assert resp.status_code == 422

    def test_patch_empty_body_is_noop(self, client) -> None:
        client.post(API, json=PAYLOAD)
        resp = client.patch(f"{API}/LOGIN-001", json={})
        assert resp.status_code == 200
        assert resp.json()["priority"] == "P0"

    @pytest.mark.parametrize("field", ["title", "tags"])
    def test_patch_null_on_not_null_column_returns_422(self, client, field: str) -> None:
        """NOT NULL 列置空必须在 Schema 层拦下，不能等到 flush 炸 500。"""
        client.post(API, json=PAYLOAD)
        resp = client.patch(f"{API}/LOGIN-001", json={field: None})
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "validation_error"


class TestDelete:
    def test_delete_returns_204(self, client) -> None:
        client.post(API, json=PAYLOAD)
        resp = client.delete(f"{API}/LOGIN-001")
        assert resp.status_code == 204
        assert client.get(f"{API}/LOGIN-001").status_code == 404

    def test_delete_unknown_returns_404(self, client) -> None:
        assert client.delete(f"{API}/NOPE-1").status_code == 404
