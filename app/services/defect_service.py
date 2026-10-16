"""缺陷服务。"""

from __future__ import annotations

from app.models import Defect
from app.services.base import EntityService


class DefectService(EntityService[Defect]):
    """缺陷 CRUD + 元数据过滤 + 关键词检索。"""

    model = Defect
    label = "缺陷"
    unique_field = "bug_id"
    equality_filters = ("module", "version", "severity", "priority", "status")
    search_fields = ("title", "reproduce_steps", "root_cause", "resolution")


__all__ = ["DefectService"]
