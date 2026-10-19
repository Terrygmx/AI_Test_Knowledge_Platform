"""CSV / Excel 表格解析与实体导入。

导入策略：

- 列名归一化为 snake_case，兼容 ``Case ID`` / ``case_id``；
- 同一文件内重复业务编号只保留第一条并计入 skipped；
- 已存在的业务编号做局部更新，而不是重复插入；
- 整个文件在一个事务中处理，任意一行校验失败则回滚整批。

``document`` 行的特殊处理：``documents`` 表只存元数据，没有 ``content`` 列，
因此表格里的 ``content`` 会落成一个 ``document`` Chunk（正文即检索单元），
而不是被 Pydantic 静默丢弃。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import ValidationError
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.exceptions import DomainError
from app.core.logging import get_logger
from app.models import Defect, Document, KnowledgeChunk, Requirement, TestCase
from app.retrieval.embedding import content_sha256
from app.schemas.defect import DefectCreate, DefectUpdate
from app.schemas.document import DocumentCreate, DocumentUpdate
from app.schemas.requirement import RequirementCreate, RequirementUpdate
from app.schemas.test_case import TestCaseCreate, TestCaseUpdate
from app.services.defect_service import DefectService
from app.services.document_service import DocumentService
from app.services.knowledge_chunk_service import KnowledgeChunkService
from app.services.requirement_service import RequirementService
from app.services.testcase_service import TestCaseService

EntityType = Literal["testcase", "defect", "requirement", "document"]

_ID_COLUMNS: dict[EntityType, str] = {
    "testcase": "case_id",
    "defect": "bug_id",
    "requirement": "req_id",
    "document": "doc_id",
}
_ARRAY_COLUMNS = {"tags", "related_cases"}
_EMPTY_VALUES = {"", "nan", "none", "null", "nat"}
#: Document 元数据 Schema 不接受该列，正文改为写入 KnowledgeChunk
_DOCUMENT_BODY_COLUMN = "content"
#: ``Type`` 在用例表里是 case_type，在文档表里语义是 doc_type
_DOCUMENT_TYPE_ALIASES = {"case_type": "doc_type"}

logger = get_logger(__name__)


@dataclass(frozen=True)
class TableImportResult:
    """一次表格导入的统计。"""

    created: int = 0
    updated: int = 0
    skipped: int = 0
    #: document 行写入 / 替换的正文 Chunk 数（向量需另行生成）
    document_chunks: int = 0
    #: 被替换掉的「已生成向量」document Chunk 数；> 0 表示需要重新补向量
    indexed_chunks_replaced: int = 0
    entity_type: str = ""


def normalize_column(name: object) -> str:
    """把外部表头规范化为 Python/Schema 字段名。"""
    text = str(name).strip().lower()
    text = re.sub(r"[\s\-/]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    aliases = {
        "case": "case_id",
        "caseid": "case_id",
        "test_case_id": "case_id",
        "bug": "bug_id",
        "bugid": "bug_id",
        "defect_id": "bug_id",
        "req": "req_id",
        "reqid": "req_id",
        "requirement_id": "req_id",
        "doc": "doc_id",
        "docid": "doc_id",
        "document_id": "doc_id",
        "expected": "expected_result",
        "type": "case_type",
    }
    return aliases.get(text, text)


def _clean_cell(value: Any, *, column: str) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.lower() in _EMPTY_VALUES:
            return None
        if column in _ARRAY_COLUMNS:
            return [part.strip() for part in re.split(r"[|,，;；]", text) if part.strip()]
        return text
    if isinstance(value, (list, tuple)):
        return [str(part).strip() for part in value if str(part).strip()]
    return value


def load_table(path: str | Path) -> pd.DataFrame:
    """读取 CSV / Excel，返回列名已规范化的 DataFrame。"""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        try:
            frame = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError(f"CSV 必须是 UTF-8 编码：{path}") from exc
    elif suffix in {".xlsx", ".xls"}:
        frame = pd.read_excel(path, dtype=str)
    else:
        raise ValueError(f"不支持的表格格式：{suffix}，仅支持 .csv / .xlsx / .xls")
    frame.columns = [normalize_column(column) for column in frame.columns]
    return frame


def infer_entity_type(columns: list[str] | tuple[str, ...]) -> EntityType:
    """根据业务编号列识别实体类型。"""
    normalized = {normalize_column(column) for column in columns}
    for entity_type, id_column in _ID_COLUMNS.items():
        if id_column in normalized:
            return entity_type
    if "title" in normalized and "content" in normalized:
        return "document"
    raise ValueError(f"无法识别表格实体类型，列：{sorted(normalized)}")


def _row_payload(row: pd.Series) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for column, value in row.items():
        cleaned = _clean_cell(value, column=str(column))
        if cleaned is not None:
            payload[str(column)] = cleaned
    return payload


_SERVICE_BY_TYPE = {
    "testcase": TestCaseService,
    "defect": DefectService,
    "requirement": RequirementService,
    "document": DocumentService,
}
_CREATE_BY_TYPE = {
    "testcase": TestCaseCreate,
    "defect": DefectCreate,
    "requirement": RequirementCreate,
    "document": DocumentCreate,
}
_UPDATE_BY_TYPE = {
    "testcase": TestCaseUpdate,
    "defect": DefectUpdate,
    "requirement": RequirementUpdate,
    "document": DocumentUpdate,
}
_MODEL_BY_TYPE = {
    "testcase": TestCase,
    "defect": Defect,
    "requirement": Requirement,
    "document": Document,
}


def _row_validation_error(exc: ValidationError, row_number: int) -> ValueError:
    errors = "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in exc.errors()
    )
    return ValueError(f"row {row_number}: {errors}")


def _sync_document_chunk(
    session: Session,
    *,
    doc_id: str,
    payload: dict[str, Any],
) -> tuple[int, int]:
    """把表格里的 ``content`` 写成 Document Chunk。

    ``documents`` 表只有元数据，没有 ``content`` 列；若直接把该列交给
    ``DocumentCreate``，Pydantic 默认会静默忽略多余字段，正文就无声丢失了。
    因此正文显式落到 ``knowledge_chunks``（正文即检索单元），并按来源替换，
    保证重复导入同一 ``doc_id`` 不会产生重复 Chunk。

    返回 ``(写入的 Chunk 数, 被清掉的已向量化 Chunk 数)``。
    """
    body = payload.get(_DOCUMENT_BODY_COLUMN)
    if not isinstance(body, str) or not body.strip():
        return 0, 0

    # 与 ImportService 一致：替换会清掉已有向量，必须显式提示补救入口
    indexed_before = KnowledgeChunkService.count_with_embedding(session, source_id=doc_id)
    session.execute(
        delete(KnowledgeChunk).where(
            KnowledgeChunk.source_type == "document",
            KnowledgeChunk.source_id == doc_id,
        )
    )
    session.add(
        KnowledgeChunk(
            source_type="document",
            source_id=doc_id,
            document_id=doc_id,
            chunk_index=0,
            content=body.strip(),
            meta={
                "title": payload.get("title"),
                "doc_type": payload.get("doc_type"),
                "source": "table",
            },
            content_hash=content_sha256(body.strip()),
            version=payload.get("version"),
        )
    )
    session.flush()
    if indexed_before:
        logger.warning(
            "表格导入替换了已向量化的 Chunk：doc_id=%s chunks=%s，"
            "需重新执行 scripts/index_entities.py --only document 补向量",
            doc_id,
            indexed_before,
        )
    return 1, indexed_before


class TableImporter:
    """把表格行规范化为业务实体并幂等写入。"""

    @classmethod
    def import_file(
        cls,
        session: Session,
        path: str | Path,
        *,
        entity_type: EntityType | None = None,
    ) -> TableImportResult:
        frame = load_table(path)
        resolved = entity_type or infer_entity_type(list(frame.columns))
        if resolved not in _SERVICE_BY_TYPE:
            raise ValueError(f"未知实体类型：{resolved}")

        id_column = _ID_COLUMNS[resolved]
        if id_column not in frame.columns:
            raise ValueError(f"{resolved} 表格缺少业务编号列：{id_column}")

        columns = list(frame.columns)
        if resolved == "document":
            # normalize_column 不知道实体类型，统一把 ``Type`` 归一成 case_type；
            # 文档语义下它其实是 doc_type，否则该列会被静默丢弃。
            columns = [
                _DOCUMENT_TYPE_ALIASES.get(column, column) for column in columns
            ]
            frame.columns = columns

        service = _SERVICE_BY_TYPE[resolved]
        create_schema = _CREATE_BY_TYPE[resolved]
        update_schema = _UPDATE_BY_TYPE[resolved]
        created = updated = skipped = 0
        document_chunks = 0
        indexed_chunks_replaced = 0
        seen: set[str] = set()

        try:
            for offset, (_, row) in enumerate(frame.iterrows(), start=2):
                payload = _row_payload(row)
                raw_id = payload.get(id_column)
                if not raw_id:
                    raise ValueError(f"row {offset}: {id_column} 不能为空")
                business_id = str(raw_id)
                if business_id in seen:
                    skipped += 1
                    continue
                seen.add(business_id)

                # document 的正文列不属于 Document 元数据，单独走 Chunk
                entity_payload = (
                    {
                        key: value
                        for key, value in payload.items()
                        if key != _DOCUMENT_BODY_COLUMN
                    }
                    if resolved == "document"
                    else payload
                )
                existing = service.get_by_unique(session, business_id)
                try:
                    if existing is None:
                        data = create_schema.model_validate(entity_payload)
                        obj = _MODEL_BY_TYPE[resolved](**data.to_orm_dict())
                        session.add(obj)
                        session.flush()
                        created += 1
                    else:
                        update_payload = {
                            key: value for key, value in entity_payload.items() if key != id_column
                        }
                        data = update_schema.model_validate(update_payload)
                        for key, value in data.to_orm_dict(exclude_unset=True).items():
                            setattr(existing, key, value)
                        session.flush()
                        updated += 1
                    if resolved == "document":
                        written, replaced = _sync_document_chunk(
                            session, doc_id=business_id, payload=payload
                        )
                        document_chunks += written
                        indexed_chunks_replaced += replaced
                except ValidationError as exc:
                    raise _row_validation_error(exc, offset) from exc
                except DomainError:
                    raise
                except Exception as exc:
                    raise ValueError(f"row {offset}: {exc}") from exc
        except Exception:
            session.rollback()
            raise

        session.commit()
        return TableImportResult(
            created=created,
            updated=updated,
            skipped=skipped,
            document_chunks=document_chunks,
            indexed_chunks_replaced=indexed_chunks_replaced,
            entity_type=resolved,
        )


__all__ = [
    "EntityType",
    "TableImportResult",
    "TableImporter",
    "infer_entity_type",
    "load_table",
    "normalize_column",
]
