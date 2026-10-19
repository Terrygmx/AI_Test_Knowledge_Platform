"""Phase 5 文档接入服务：解析 → 文档元数据 → Chunk。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.exceptions import DomainError
from app.core.logging import get_logger
from app.ingestion.markdown import MarkdownDocument, MarkdownParser
from app.ingestion.pdf import PdfParser
from app.models import KnowledgeChunk
from app.retrieval.embedding import content_sha256
from app.schemas.document import DocumentCreate, DocumentUpdate
from app.services.document_service import DocumentService
from app.services.knowledge_chunk_service import KnowledgeChunkService

logger = get_logger(__name__)


@dataclass(frozen=True)
class MarkdownImportResult:
    """Markdown 导入统计。"""

    document_id: str
    section_count: int
    created: bool
    #: 被本次导入替换掉的「已生成向量」Chunk 数；> 0 表示需要重新补向量
    indexed_chunks_replaced: int = 0


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
            has_content = bool(parsed.sections)
            if existing is None:
                data = DocumentCreate.model_validate(payload)
                DocumentService.create(session, data=data.to_orm_dict())
                created = True
            elif has_content:
                update_payload = {key: value for key, value in payload.items() if key != "doc_id"}
                data = DocumentUpdate.model_validate(update_payload)
                DocumentService.update(
                    session,
                    existing,
                    data=data.to_orm_dict(exclude_unset=True),
                )
                created = False
            else:
                # 空解析结果不是一次有效更新：若覆盖元数据，title/version/path 会被
                # 清空或改回 fallback，和「保留旧 Chunk」产生互相矛盾的状态。
                created = False

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
            # 空文档（0 Section）不能等价于「清空索引」：导入空文件 / 空文本 PDF 时
            # 删掉旧 Chunk 会让文档凭空变得检索不可见，且没有任何报错。
            # 此时 Document 元数据与 Chunk 都保持原状，并如实报告「没有发生替换」。
            indexed_before = 0
            if chunks:
                # 新 Chunk 的 embedding 为 None；旧 Chunk 若已完成向量化，替换会让
                # 文档重新变成检索不可见。这里计数并返回，由调用方（CLI）提示补救入口。
                indexed_before = KnowledgeChunkService.count_with_embedding(
                    session, source_id=doc_id
                )
                session.execute(
                    delete(KnowledgeChunk).where(
                        KnowledgeChunk.source_type == "document",
                        KnowledgeChunk.source_id == doc_id,
                    )
                )
                session.add_all(chunks)
                session.commit()
                if indexed_before:
                    logger.warning(
                        "重复导入替换了已向量化的 Chunk：doc_id=%s chunks=%s，"
                        "需重新执行 scripts/index_entities.py --only document 补向量",
                        doc_id,
                        indexed_before,
                    )
            else:
                session.commit()
                logger.warning(
                    "文档没有可导入的正文：doc_id=%s，保留已有 Document 与 Chunk（未修改）",
                    doc_id,
                )
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
            indexed_chunks_replaced=indexed_before,
        )


__all__ = ["ImportService", "MarkdownImportResult"]
