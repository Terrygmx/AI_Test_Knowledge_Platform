"""Phase 5 文档接入服务：解析 → 文档元数据 → Chunk。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.exceptions import DomainError
from app.ingestion.markdown import MarkdownDocument, MarkdownParser
from app.ingestion.pdf import PdfParser
from app.models import KnowledgeChunk
from app.retrieval.embedding import content_sha256
from app.schemas.document import DocumentCreate, DocumentUpdate
from app.services.document_service import DocumentService


@dataclass(frozen=True)
class MarkdownImportResult:
    """Markdown 导入统计。"""

    document_id: str
    section_count: int
    created: bool


class ImportService:
    """文档导入编排；不在此阶段调用 Embedding。"""

    @classmethod
    def import_markdown(
        cls,
        session: Session,
        *,
        doc_id: str,
        source: str,
        path: str | None = None,
        version: str | None = None,
    ) -> MarkdownImportResult:
        parsed = MarkdownParser.parse(source)
        return cls._save_document(
            session,
            doc_id=doc_id,
            parsed=parsed,
            doc_type="markdown",
            path=path,
            version=version or parsed.version,
        )

    @classmethod
    def import_pdf(
        cls,
        session: Session,
        *,
        doc_id: str,
        path: str | Path,
        version: str | None = None,
    ) -> MarkdownImportResult:
        text = PdfParser.parse(path)
        parsed = MarkdownParser.parse(text, fallback_title=Path(path).stem)
        return cls._save_document(
            session,
            doc_id=doc_id,
            parsed=parsed,
            doc_type="pdf",
            path=str(path),
            version=version,
        )

    @classmethod
    def _save_document(
        cls,
        session: Session,
        *,
        doc_id: str,
        parsed: MarkdownDocument,
        doc_type: str,
        path: str | None,
        version: str | None,
    ) -> MarkdownImportResult:
        try:
            existing = DocumentService.get_by_unique(session, doc_id)
            payload = {
                "doc_id": doc_id,
                "title": parsed.title,
                "doc_type": doc_type,
                "path": path,
                "content_hash": content_sha256(
                    "\n\n".join(section.content for section in parsed.sections)
                ),
                "version": version,
                "metadata": {
                    **parsed.metadata,
                    "section_count": len(parsed.sections),
                },
            }
            if existing is None:
                data = DocumentCreate.model_validate(payload)
                DocumentService.create(session, data=data.to_orm_dict())
                created = True
            else:
                update_payload = {key: value for key, value in payload.items() if key != "doc_id"}
                data = DocumentUpdate.model_validate(update_payload)
                DocumentService.update(
                    session,
                    existing,
                    data=data.to_orm_dict(exclude_unset=True),
                )
                created = False

            session.execute(
                delete(KnowledgeChunk).where(
                    KnowledgeChunk.source_type == "document",
                    KnowledgeChunk.source_id == doc_id,
                )
            )
            chunks = [
                KnowledgeChunk(
                    source_type="document",
                    source_id=doc_id,
                    document_id=doc_id,
                    chunk_index=index,
                    content=section.content,
                    meta={
                        "title": section.title,
                        "heading_path": list(section.path),
                        "level": section.level,
                    },
                    content_hash=content_sha256(section.content),
                    version=version,
                )
                for index, section in enumerate(parsed.sections)
            ]
            session.add_all(chunks)
            session.commit()
        except DomainError:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            raise

        return MarkdownImportResult(
            document_id=doc_id,
            section_count=len(parsed.sections),
            created=created,
        )


__all__ = ["ImportService", "MarkdownImportResult"]
