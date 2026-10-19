"""OpenAPI 契约检查：查询参数应保留受控词表文档。"""

from __future__ import annotations


class TestOpenAPI:
    def test_search_exposes_severity_and_enum_priority(self, client) -> None:
        schema = client.get("/openapi.json").json()
        params = schema["paths"]["/api/v1/search"]["get"]["parameters"]
        names = {param["name"] for param in params}
        assert "severity" in names
        priority = next(param for param in params if param["name"] == "priority")
        ref = priority["schema"]["anyOf"][0]["$ref"]
        assert ref == "#/components/schemas/Priority"
        assert schema["components"]["schemas"]["Priority"]["enum"] == ["P0", "P1", "P2", "P3"]
