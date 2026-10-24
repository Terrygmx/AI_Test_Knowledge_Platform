"""Phase 12 Evaluation Dataset 契约。

Phase 6-11 的 ``ChunkingDataset`` 服务于 Chunking / Retrieval 实验，不能直接
扩展成 Phase 12 的 Gold Set：后者需要分类配额、候选 / 人工确认状态、意图、
文档与 Chunk 双层证据以及答案要点。这里保持独立契约，避免破坏既有实验。

关键边界：

- AI 只能生成 ``pending`` 候选；正式评测必须由人工逐条确认；
- ``gold_documents`` 与 ``gold_chunks`` 都必须能回到语料中的真实来源；
- 证据必须是来源正文（文档级）或对应 Chunk 正文中的原文片段；
- ``expected_filters`` 是评测条件，不能把被过滤掉的 Gold 当成可回答。
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.evaluation.dataset import ChunkingDataset, load_dataset
from app.evaluation.lexical_experiment import RetrievedChunk, retrieve_chunks
from app.evaluation.metadata_filter_experiment import matches_metadata
from app.evaluation.metrics import normalize_text
from app.retrieval.filters import MetadataFilterError, normalize_metadata_filters
from app.schemas.common import BaseSchema, SourceType


class Phase12Category(StrEnum):
    """计划中约定的六类 Query。"""

    TESTCASE_SEARCH = "testcase_search"
    DEFECT_SEARCH = "defect_search"
    DOCUMENT_QA = "document_qa"
    CROSS_VERSION = "cross_version"
    MULTI_CONDITION = "multi_condition"
    UNANSWERABLE = "unanswerable"


CATEGORY_COUNTS: dict[Phase12Category, int] = {
    Phase12Category.TESTCASE_SEARCH: 30,
    Phase12Category.DEFECT_SEARCH: 20,
    Phase12Category.DOCUMENT_QA: 20,
    Phase12Category.CROSS_VERSION: 10,
    Phase12Category.MULTI_CONDITION: 10,
    Phase12Category.UNANSWERABLE: 10,
}


class ReviewStatus(StrEnum):
    """候选问题的审阅状态。"""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


REVIEWED_BY = "Terry"


class GoldDocument(BaseSchema):
    """Gold 来源级证据。

    名称沿用计划中的 ``gold_documents``；``source_type`` 可以是四类业务实体，
    不把 TestCase / Defect 查询强行改写成文档。
    """

    source_type: SourceType
    source_id: str = Field(min_length=1)
    evidence: str = Field(min_length=1)


class GoldChunk(BaseSchema):
    """Gold Chunk 级证据；Chunk 序号必须能在当前语料中定位。"""

    source_type: SourceType
    source_id: str = Field(min_length=1)
    chunk_index: int = Field(ge=0)
    evidence: str = Field(min_length=1)


class Phase12Query(BaseSchema):
    """一条 Phase 12 Query 及其候选 / 人工确认元数据。"""

    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    query_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    category: Phase12Category
    expected_intent: str = Field(min_length=1)
    expected_filters: dict[str, str | list[str]] = Field(default_factory=dict)
    gold_documents: list[GoldDocument] = Field(default_factory=list)
    gold_chunks: list[GoldChunk] = Field(default_factory=list)
    expected_answer_points: list[str] = Field(default_factory=list)
    answerable: bool = True
    generated_by: str = Field(min_length=1)
    review_status: ReviewStatus = ReviewStatus.PENDING
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    review_notes: str | None = None

    @model_validator(mode="after")
    def _validate_answerability(self) -> Phase12Query:
        is_unanswerable = self.category is Phase12Category.UNANSWERABLE
        if is_unanswerable and self.answerable:
            raise ValueError("unanswerable 分类必须设置 answerable=false")
        if not is_unanswerable and not self.answerable:
            raise ValueError("只有 unanswerable 分类可以设置 answerable=false")

        if self.answerable:
            if not self.gold_documents:
                raise ValueError("可回答问题必须标注 gold_documents")
            if not self.gold_chunks:
                raise ValueError("可回答问题必须标注 gold_chunks")
            if not self.expected_answer_points:
                raise ValueError("可回答问题必须标注 expected_answer_points")
        else:
            if self.gold_documents or self.gold_chunks:
                raise ValueError("不可回答问题不应标注 Gold 来源")
            if self.expected_answer_points:
                raise ValueError("不可回答问题不应标注答案要点")
            if self.expected_filters:
                raise ValueError("不可回答问题不应设置 expected_filters")
        return self

    @model_validator(mode="after")
    def _validate_expected_filters(self) -> Phase12Query:
        try:
            self.expected_filters = normalize_metadata_filters(self.expected_filters)
        except MetadataFilterError as exc:
            raise ValueError(f"expected_filters 不合法：{exc}") from exc
        return self

    @model_validator(mode="after")
    def _validate_review_provenance(self) -> Phase12Query:
        if self.review_status is ReviewStatus.PENDING:
            if self.reviewed_by is not None or self.reviewed_at is not None:
                raise ValueError("pending 候选不能带审阅人 / 审阅时间")
            if self.review_notes is not None:
                raise ValueError("pending 候选不能带审阅备注")
            return self

        if not self.reviewed_by:
            raise ValueError(f"{self.review_status.value} 状态必须记录 reviewed_by")
        if self.reviewed_by != REVIEWED_BY:
            raise ValueError(f"审阅人必须是 {REVIEWED_BY}")
        if not self.review_notes or not self.review_notes.strip():
            raise ValueError(f"{self.review_status.value} 状态必须记录 review_notes")
        if self.reviewed_at is None:
            raise ValueError(f"{self.review_status.value} 状态必须记录 reviewed_at")
        if self.reviewed_at.tzinfo is None or self.reviewed_at.utcoffset() is None:
            raise ValueError("reviewed_at 必须带时区")
        return self

    @field_validator("expected_answer_points")
    @classmethod
    def _validate_answer_points(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("expected_answer_points 不能包含空白项")
        normalized = [normalize_text(value) for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("expected_answer_points 不能重复")
        return values


class Phase12Dataset(BaseSchema):
    """Phase 12 候选 / Gold Set 文件。"""

    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    version: str = Field(min_length=1)
    description: str = Field(min_length=1)
    corpus_dataset: str = Field(min_length=1)
    generation_method: str = Field(min_length=1)
    generated_at: datetime
    queries: list[Phase12Query]

    @model_validator(mode="after")
    def _validate_query_ids_and_text(self) -> Phase12Dataset:
        query_ids = [query.query_id for query in self.queries]
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("Phase 12 query_id 重复")

        normalized_queries = [normalize_text(query.query) for query in self.queries]
        if len(normalized_queries) != len(set(normalized_queries)):
            raise ValueError("Phase 12 query 文本重复")
        return self

    @property
    def dataset_sha256(self) -> str:
        payload = self.model_dump(mode="json")
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Phase12DatasetError(ValueError):
    """Phase 12 数据集无法用于候选 / 正式评测。"""


def category_counts(dataset: Phase12Dataset) -> dict[Phase12Category, int]:
    """返回实际分类计数，包含缺失分类的 0。"""
    actual = Counter(query.category for query in dataset.queries)
    return {category: actual.get(category, 0) for category in CATEGORY_COUNTS}


def _source_texts(corpus: ChunkingDataset) -> dict[tuple[str, str], str]:
    """来源级正文；文档使用完整 Markdown，而不是单个 Section。"""
    chunks = retrieve_chunks(corpus)
    texts: dict[tuple[str, str], str] = {}
    for chunk in chunks:
        if chunk.source_type != "document":
            texts[(chunk.source_type, chunk.source_id)] = chunk.content
    for document in corpus.documents:
        texts[("document", document.doc_id)] = document.content
    return texts


def _chunk_map(corpus: ChunkingDataset) -> dict[tuple[str, str, int], RetrievedChunk]:
    return {
        (chunk.source_type, chunk.source_id, chunk.chunk_index): chunk
        for chunk in retrieve_chunks(corpus)
    }


def _contains_evidence(text: str, evidence: str) -> bool:
    needle = normalize_text(evidence)
    return bool(needle) and needle in normalize_text(text)


def _validate_corpus_references(
    dataset: Phase12Dataset,
    corpus: ChunkingDataset,
) -> None:
    source_texts = _source_texts(corpus)
    chunks = _chunk_map(corpus)

    for query in dataset.queries:
        document_sources = {(item.source_type, item.source_id) for item in query.gold_documents}
        chunk_sources = {(item.source_type, item.source_id) for item in query.gold_chunks}

        if chunk_sources - document_sources:
            missing = sorted(chunk_sources - document_sources)
            raise Phase12DatasetError(
                f"{query.query_id} 的 gold_chunks 缺少对应 gold_documents：{missing}"
            )
        if document_sources - chunk_sources:
            missing = sorted(document_sources - chunk_sources)
            raise Phase12DatasetError(
                f"{query.query_id} 的 gold_documents 缺少对应 gold_chunks：{missing}"
            )

        if (
            query.category
            in {
                Phase12Category.CROSS_VERSION,
                Phase12Category.MULTI_CONDITION,
            }
            and not query.expected_filters
        ):
            raise Phase12DatasetError(
                f"{query.query_id} 的 {query.category.value} 分类必须标注 expected_filters"
            )

        seen_documents: set[tuple[str, str, str]] = set()
        for item in query.gold_documents:
            key = (item.source_type, item.source_id)
            if key not in source_texts:
                raise Phase12DatasetError(
                    f"{query.query_id} 引用了不存在的来源：{item.source_type}:{item.source_id}"
                )
            if not _contains_evidence(source_texts[key], item.evidence):
                raise Phase12DatasetError(
                    f"{query.query_id} 的文档证据不在来源正文中：{item.source_type}:{item.source_id}"
                )
            marker = (item.source_type, item.source_id, normalize_text(item.evidence))
            if marker in seen_documents:
                raise Phase12DatasetError(f"{query.query_id} 的 gold_documents 存在重复证据")
            seen_documents.add(marker)

        seen_chunks: set[tuple[str, str, int, str]] = set()
        for item in query.gold_chunks:
            key = (item.source_type, item.source_id, item.chunk_index)
            chunk = chunks.get(key)
            if chunk is None:
                raise Phase12DatasetError(
                    f"{query.query_id} 引用了不存在的 Chunk："
                    f"{item.source_type}:{item.source_id}#{item.chunk_index}"
                )
            if not _contains_evidence(chunk.content, item.evidence):
                raise Phase12DatasetError(
                    f"{query.query_id} 的 Chunk 证据不在目标 Chunk 中："
                    f"{item.source_type}:{item.source_id}#{item.chunk_index}"
                )
            if query.expected_filters and not matches_metadata(
                chunk.meta or {},
                query.expected_filters,
            ):
                raise Phase12DatasetError(
                    f"{query.query_id} 的 expected_filters 会过滤掉 Gold Chunk："
                    f"{item.source_type}:{item.source_id}#{item.chunk_index}"
                )
            marker = (
                item.source_type,
                item.source_id,
                item.chunk_index,
                normalize_text(item.evidence),
            )
            if marker in seen_chunks:
                raise Phase12DatasetError(f"{query.query_id} 的 gold_chunks 存在重复证据")
            seen_chunks.add(marker)


def validate_phase12_dataset(
    dataset: Phase12Dataset,
    corpus: ChunkingDataset,
    *,
    mode: Literal["candidate", "formal"] = "candidate",
) -> None:
    """执行依赖语料的校验，并按模式检查人工确认门槛。"""
    if mode not in {"candidate", "formal"}:
        raise ValueError("mode 必须是 candidate 或 formal")

    if len(dataset.queries) != sum(CATEGORY_COUNTS.values()):
        raise Phase12DatasetError(
            f"Phase 12 必须包含 {sum(CATEGORY_COUNTS.values())} 条 Query，"
            f"实际为 {len(dataset.queries)}"
        )

    actual_counts = category_counts(dataset)
    if actual_counts != CATEGORY_COUNTS:
        raise Phase12DatasetError(
            "Phase 12 分类数量不符合计划："
            f"expected={ {key.value: value for key, value in CATEGORY_COUNTS.items()} }, "
            f"actual={ {key.value: value for key, value in actual_counts.items()} }"
        )

    _validate_corpus_references(dataset, corpus)

    if mode == "formal":
        not_confirmed = [
            query.query_id
            for query in dataset.queries
            if query.review_status is not ReviewStatus.CONFIRMED
        ]
        if not_confirmed:
            preview = ", ".join(not_confirmed[:5])
            suffix = "..." if len(not_confirmed) > 5 else ""
            raise Phase12DatasetError(
                f"正式评测要求全部 Query 为 confirmed；仍有 {len(not_confirmed)} 条未确认："
                f"{preview}{suffix}"
            )


def _resolve_corpus_path(dataset_path: Path, corpus_dataset: str) -> Path:
    corpus_path = Path(corpus_dataset)
    if corpus_path.is_absolute():
        return corpus_path

    beside_dataset = dataset_path.parent / corpus_path
    if beside_dataset.exists():
        return beside_dataset
    if corpus_path.exists():
        return corpus_path
    raise Phase12DatasetError(f"找不到 Phase 12 语料文件：{corpus_dataset}")


def load_phase12_dataset(
    path: str | Path,
    *,
    corpus: ChunkingDataset | None = None,
    mode: Literal["candidate", "formal"] = "candidate",
) -> Phase12Dataset:
    """读取并校验 Phase 12 数据集；解析错误不静默修正。

    显式传入 ``corpus`` 时优先使用它，方便测试或 CLI 固定语料路径；
    未传入时仍从数据集记录的相对路径回退解析。
    """
    dataset_path = Path(path)
    raw: Any = json.loads(dataset_path.read_text(encoding="utf-8"))
    dataset = Phase12Dataset.model_validate(raw)

    if corpus is None:
        corpus_path = _resolve_corpus_path(dataset_path, dataset.corpus_dataset)
        corpus = load_dataset(corpus_path)
    validate_phase12_dataset(dataset, corpus, mode=mode)
    return dataset


def phase12_to_chunking_dataset(
    dataset: Phase12Dataset,
    corpus: ChunkingDataset | None = None,
) -> ChunkingDataset:
    """把 Phase 12 数据集投影成旧评测器可消费的 Query 契约。

    Phase 13 复用现有检索实验时只需要 ``relevant`` 证据。这里只做显式、
    单向投影，不修改 Phase 12 原始契约，也不改变审阅状态或配额校验。
    传入 ``corpus`` 时会保留原始 TestCase / Defect / Requirement / Document，
    因为旧契约会校验 Gold 来源是否存在于同一数据集内。
    """
    from app.evaluation.dataset import EvaluationQuery, EvidenceLabel

    queries = [
        EvaluationQuery(
            query_id=query.query_id,
            query=query.query,
            relevant=[
                EvidenceLabel(
                    source_type=label.source_type,
                    source_id=label.source_id,
                    evidence=label.evidence,
                )
                for label in query.gold_documents
            ],
            answerable=query.answerable,
            category=query.category.value,
            expected_filters=query.expected_filters,
        )
        for query in dataset.queries
    ]
    if corpus is None:
        raise Phase12DatasetError("phase12_to_chunking_dataset 必须传入语料以保留来源")
    return corpus.model_copy(
        update={
            "version": dataset.version,
            "description": dataset.description,
            "queries": queries,
        }
    )


__all__ = [
    "CATEGORY_COUNTS",
    "GoldChunk",
    "GoldDocument",
    "Phase12Category",
    "Phase12Dataset",
    "Phase12DatasetError",
    "Phase12Query",
    "ReviewStatus",
    "category_counts",
    "load_phase12_dataset",
    "phase12_to_chunking_dataset",
    "validate_phase12_dataset",
]
