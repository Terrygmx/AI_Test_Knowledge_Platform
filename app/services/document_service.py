"""文档服务。

只管理文档**元数据**；正文切分与索引起 Phase 2（Chunker / Embedding）之后才接入。
"""

from __future__ import annotations

from app.models import Document
from app.services.base import EntityService


class DocumentService(EntityService[Document]):
    """文档元数据 CRUD。"""

    model = Document
    label = "文档"
    unique_field = "doc_id"
    equality_filters = ("doc_type", "version")
    search_fields = ("title", "path")


__all__ = ["DocumentService"]
