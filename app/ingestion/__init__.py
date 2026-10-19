"""数据接入（Phase 5）：Parser → Normalizer → Chunker。"""

from app.ingestion.markdown import MarkdownDocument, MarkdownParser, MarkdownSection
from app.ingestion.pdf import PdfParser
from app.ingestion.service import ImportService, MarkdownImportResult
from app.ingestion.table import (
    EntityType,
    TableImporter,
    TableImportResult,
    infer_entity_type,
    load_table,
    normalize_column,
)

__all__ = [
    "EntityType",
    "ImportService",
    "MarkdownDocument",
    "MarkdownImportResult",
    "MarkdownParser",
    "MarkdownSection",
    "PdfParser",
    "TableImportResult",
    "TableImporter",
    "infer_entity_type",
    "load_table",
    "normalize_column",
]
