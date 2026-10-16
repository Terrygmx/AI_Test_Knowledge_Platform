"""统一检索 API 测试。"""

from __future__ import annotations

API = "/api/v1/search"
CASES = "/api/v1/testcases"
DEFECTS = "/api/v1/defects"


def _seed(client) -> None:
    client.post(
        CASES,
        json={
            "case_id": "PAY-001",
            "title": "支付失败重试",
            "module": "payment",
            "priority": "P0",
            "steps": "模拟支付失败后点击重试",
        },
    )
    client.post(
        CASES,
        json={
            "case_id": "LOGIN-001",
            "title": "短信验证码登录",
            "module": "login",
            "priority": "P1",
        },
    )
    client.post(
        DEFECTS,
        json={
            "bug_id": "BUG-1021",
            "title": "支付失败后未重置订单状态",
            "module": "payment",
            "priority": "P0",
            "severity": "critical",
            "status": "fixed",
        },
    )


class TestSearchAPI:
    def test_search_all(self, client) -> None:
        _seed(client)
        body = client.get(API).json()
        assert body["total"] == 3
        assert [h["source_type"] for h in body["items"]] == ["testcase", "testcase", "defect"]
        assert body["items"][0]["source_id"] == "PAY-001"

    def test_search_keyword(self, client) -> None:
        _seed(client)
        body = client.get(API, params={"q": "支付失败"}).json()
        assert body["total"] == 2
        assert {h["source_type"] for h in body["items"]} == {"testcase", "defect"}

    def test_search_filters(self, client) -> None:
        _seed(client)
        body = client.get(API, params={"module": "payment"}).json()
        assert body["total"] == 2
        body = client.get(API, params={"priority": "P0"}).json()
        assert body["total"] == 2
        body = client.get(API, params={"status": "fixed"}).json()
        assert body["total"] == 1

    def test_search_source_type_filter(self, client) -> None:
        _seed(client)
        body = client.get(API, params={"source_type": "defect"}).json()
        assert body["total"] == 1
        assert body["items"][0]["source_id"] == "BUG-1021"

    def test_search_multiple_source_types(self, client) -> None:
        _seed(client)
        body = client.get(API + "?source_type=testcase&source_type=defect").json()
        assert body["total"] == 3

    def test_search_type_alias_maps_to_case_type(self, client) -> None:
        _seed(client)
        body = client.get(API, params={"type": "smoke"}).json()
        assert body["total"] == 0
        client.post(CASES, json={"case_id": "S-1", "title": "冒烟", "case_type": "smoke"})
        body = client.get(API, params={"type": "smoke"}).json()
        assert body["total"] == 1

    def test_search_pagination(self, client) -> None:
        _seed(client)
        body = client.get(API, params={"limit": 2}).json()
        assert len(body["items"]) == 2
        assert body["total"] == 3
        body = client.get(API, params={"limit": 2, "offset": 2}).json()
        assert len(body["items"]) == 1

    def test_search_response_has_no_score_field(self, client) -> None:
        """Phase 1 不返回相似度：没有打分依据就不编数字（Evidence First）。"""
        _seed(client)
        body = client.get(API, params={"q": "支付"}).json()
        for hit in body["items"]:
            assert "score" not in hit
            assert hit["matched_on"]

    def test_search_reports_effective_filters(self, client) -> None:
        _seed(client)
        body = client.get(API, params={"module": "payment", "version": "v1.3"}).json()
        assert body["filters"] == {"module": "payment", "version": "v1.3"}

    def test_search_invalid_source_type_422(self, client) -> None:
        _seed(client)
        resp = client.get(API, params={"source_type": "nope"})
        assert resp.status_code == 422

    def test_search_empty(self, client) -> None:
        body = client.get(API).json()
        assert body["items"] == []
        assert body["total"] == 0
