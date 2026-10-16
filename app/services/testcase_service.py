"""测试用例服务。"""

from __future__ import annotations

from app.models import TestCase
from app.services.base import EntityService


class TestCaseService(EntityService[TestCase]):
    """用例 CRUD + 元数据过滤 + 关键词检索。"""

    __test__ = False  # 类名以 Test 开头，避免被 pytest 误收集

    model = TestCase
    label = "测试用例"
    unique_field = "case_id"
    equality_filters = ("module", "sub_module", "version", "priority", "case_type")
    search_fields = ("title", "precondition", "steps", "expected_result")


__all__ = ["TestCaseService"]
