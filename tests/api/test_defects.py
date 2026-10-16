"""缺陷 API 测试。"""

from __future__ import annotations

import pytest

API = "/api/v1/defects"

PAYLOAD = {
    "bug_id": "BUG-1021",
    "title": "支付失败后订单状态未重置",
    "module": "payment",
    "version": "v1.3",
    "severity": "critical",
    "priority": "P0",
    "status": "fixed",
    "reproduce_steps": "1. 下单 2. 支付失败 3. 查看订单",
    "root_cause": "支付回调未处理失败分支",
    "resolution": "补充状态机重置逻辑",
    "related_cases": ["PAY-001", "PAY-002"],
}


class TestDefectAPI:
    def test_create(self, client) -> None:
        resp = client.post(API, json=PAYLOAD)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["bug_id"] == "BUG-1021"
        assert body["severity"] == "critical"
        assert body["related_cases"] == ["PAY-001", "PAY-002"]

    def test_create_normalizes_case(self, client) -> None:
        resp = client.post(API, json={**PAYLOAD, "severity": "Critical", "status": "In_Progress"})
        assert resp.status_code == 201
        assert resp.json()["severity"] == "critical"
        assert resp.json()["status"] == "in_progress"

    def test_create_duplicate_409(self, client) -> None:
        client.post(API, json=PAYLOAD)
        resp = client.post(API, json=PAYLOAD)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "duplicate"

    def test_create_invalid_bug_id_422(self, client) -> None:
        resp = client.post(API, json={**PAYLOAD, "bug_id": "bug 1021"})
        assert resp.status_code == 422

    def test_get_and_404(self, client) -> None:
        client.post(API, json=PAYLOAD)
        assert client.get(f"{API}/BUG-1021").status_code == 200
        assert client.get(f"{API}/BUG-9999").status_code == 404

    @pytest.fixture()
    def seeded(self, client):
        rows = [
            {
                **PAYLOAD,
                "bug_id": "BUG-1",
                "module": "payment",
                "severity": "critical",
                "status": "open",
            },
            {
                **PAYLOAD,
                "bug_id": "BUG-2",
                "module": "payment",
                "severity": "major",
                "status": "fixed",
            },
            {
                **PAYLOAD,
                "bug_id": "BUG-3",
                "module": "login",
                "severity": "major",
                "status": "open",
            },
        ]
        for row in rows:
            assert client.post(API, json=row).status_code == 201
        return rows

    def test_list_and_filters(self, client, seeded) -> None:
        assert client.get(API).json()["total"] == 3
        assert client.get(API, params={"module": "payment"}).json()["total"] == 2
        assert client.get(API, params={"severity": "critical"}).json()["total"] == 1
        assert client.get(API, params={"status": "open"}).json()["total"] == 2
        assert client.get(API, params={"module": "payment", "status": "fixed"}).json()["total"] == 1

    def test_keyword_search_on_resolution(self, client, seeded) -> None:
        body = client.get(API, params={"q": "状态机"}).json()
        assert body["total"] == 3  # 三条缺陷的 resolution 都来自同一模板

    def test_patch_and_delete(self, client, seeded) -> None:
        resp = client.patch(f"{API}/BUG-1", json={"status": "closed"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "closed"
        assert client.delete(f"{API}/BUG-1").status_code == 204
        assert client.get(f"{API}/BUG-1").status_code == 404

    @pytest.mark.parametrize("field", ["title", "related_cases"])
    def test_patch_null_on_not_null_column_returns_422(self, client, seeded, field: str) -> None:
        resp = client.patch(f"{API}/BUG-1", json={field: None})
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "validation_error"
