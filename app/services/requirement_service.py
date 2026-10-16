"""需求服务。"""

from __future__ import annotations

from app.models import Requirement
from app.services.base import EntityService


class RequirementService(EntityService[Requirement]):
    """需求 CRUD + 元数据过滤 + 关键词检索。"""

    model = Requirement
    label = "需求"
    unique_field = "req_id"
    equality_filters = ("module", "version", "priority", "status")
    search_fields = ("title", "content")


__all__ = ["RequirementService"]
