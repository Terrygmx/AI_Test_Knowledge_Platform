"""Phase 10 Metadata Filter 的跨方言契约。"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.evaluation.dataset import EvaluationQuery, load_dataset
from app.models import KnowledgeChunk
from app.retrieval.embedding import content_sha256
from app.retrieval.filters import (
    ARRAY_FILTER_FIELDS,
    MetadataFilterError,
    apply_metadata_filters,
    normalize_metadata_filters,
)


def _chunk(
    source_id: str,
    *,
    source_type: str = "testcase",
    meta: dict[str, object] | None = None,
) -> KnowledgeChunk:
    return KnowledgeChunk(
        source_type=source_type,
        source_id=source_id,
        chunk_index=0,
        content=source_id,
        content_hash=content_sha256(source_id),
        meta=meta or {},
    )


class TestMetadataFilterNormalization:
    def test_empty_filters_are_a_noop(self) -> None:
        assert normalize_metadata_filters(None) == {}
        assert normalize_metadata_filters({}) == {}

    @pytest.mark.parametrize("filters", [[], "", 0, False])
    def test_non_mapping_filters_fail_fast(self, filters: object) -> None:
        with pytest.raises(MetadataFilterError, match="对象"):
            normalize_metadata_filters(filters)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "field",
        [
            "module",
            "sub_module",
            "priority",
            "version",
            "status",
            "severity",
            "case_type",
            "doc_type",
            "tags",
            "related_cases",
        ],
    )
    def test_supported_fields_are_public(self, field: str) -> None:
        assert normalize_metadata_filters(
            {field: "x" if field not in {"tags", "related_cases"} else ["x"]}
        )

    def test_unknown_field_fails_fast(self) -> None:
        with pytest.raises(MetadataFilterError, match="未知"):
            normalize_metadata_filters({"moduel": "login"})

    def test_blank_scalar_is_rejected(self) -> None:
        with pytest.raises(MetadataFilterError, match="空白"):
            normalize_metadata_filters({"module": "  "})

    def test_empty_array_is_rejected(self) -> None:
        with pytest.raises(MetadataFilterError, match="不能为空"):
            normalize_metadata_filters({"tags": []})

    def test_array_values_must_be_non_blank_strings(self) -> None:
        with pytest.raises(MetadataFilterError, match="字符串"):
            normalize_metadata_filters({"tags": ["smoke", 1]})
        with pytest.raises(MetadataFilterError, match="空白"):
            normalize_metadata_filters({"related_cases": [" ", "TC-1"]})

    def test_scalar_fields_do_not_accept_arrays(self) -> None:
        with pytest.raises(MetadataFilterError, match="标量"):
            normalize_metadata_filters({"module": ["login", "payment"]})

    def test_array_fields_do_not_accept_scalar_values(self) -> None:
        with pytest.raises(MetadataFilterError, match="数组"):
            normalize_metadata_filters({"tags": "smoke"})

    def test_normalization_strips_values_and_deduplicates_arrays(self) -> None:
        assert normalize_metadata_filters(
            {
                "module": " login ",
                "tags": [" smoke ", "smoke", "security"],
            }
        ) == {"module": "login", "tags": ["smoke", "security"]}

    def test_filters_are_immutable_from_the_caller(self) -> None:
        source = {"tags": ["smoke"]}
        normalized = normalize_metadata_filters(source)
        normalized["tags"].append("security")
        assert source == {"tags": ["smoke"]}

    def test_scalar_and_array_filters_are_combined_with_and(self) -> None:
        assert normalize_metadata_filters({"module": "login", "tags": ["smoke", "security"]}) == {
            "module": "login",
            "tags": ["smoke", "security"],
        }

    def test_dataset_filters_are_validated_and_normalized(self) -> None:
        query = EvaluationQuery(
            query_id="Q",
            query="x",
            answerable=False,
            expected_filters={"module": " login ", "tags": [" smoke ", "smoke"]},
        )
        assert query.expected_filters == {"module": "login", "tags": ["smoke"]}

        with pytest.raises(ValueError, match="expected_filters"):
            EvaluationQuery(
                query_id="Q",
                query="x",
                answerable=False,
                expected_filters={"unknown": "x"},
            )

    def test_dataset_filter_never_excludes_a_gold_evidence_source(self) -> None:
        """Phase 10 Gold 过滤条件必须与人工标注证据一致。"""
        from app.models import Defect, Requirement, TestCase
        from app.services.index_service import (
            case_to_chunk,
            defect_to_chunk,
            requirement_to_chunk,
        )

        dataset = load_dataset("eval/fixtures/phase10_simulated.json")
        metadata = {}
        for seed in dataset.testcases:
            chunk = case_to_chunk(TestCase(**seed.model_dump()))
            metadata[(chunk.source_type, chunk.source_id)] = chunk.metadata
        for seed in dataset.defects:
            chunk = defect_to_chunk(Defect(**seed.model_dump()))
            metadata[(chunk.source_type, chunk.source_id)] = chunk.metadata
        for seed in dataset.requirements:
            chunk = requirement_to_chunk(Requirement(**seed.model_dump()))
            metadata[(chunk.source_type, chunk.source_id)] = chunk.metadata
        for seed in dataset.documents:
            metadata[("document", seed.doc_id)] = {
                "doc_type": seed.doc_type,
                "version": seed.version,
            }

        for query in dataset.queries:
            for label in query.relevant:
                values = metadata[(label.source_type, label.source_id)]
                for field, expected in query.expected_filters.items():
                    if field in ARRAY_FILTER_FIELDS:
                        assert set(expected) & set(values.get(field) or []), (
                            query.query_id,
                            field,
                        )
                    else:
                        assert values.get(field) == expected, (query.query_id, field)


class TestMetadataFilterSQL:
    """同一组语义在 SQLite 与 PostgreSQL 上都必须成立。"""

    def test_scalar_filter_matches_exactly(self, db_session) -> None:
        db_session.add_all(
            [
                _chunk("LOGIN", meta={"module": "login"}),
                _chunk("PAYMENT", meta={"module": "payment"}),
                _chunk("MISSING"),
            ]
        )
        db_session.commit()

        stmt = apply_metadata_filters(
            select(KnowledgeChunk),
            {"module": "login"},
            dialect_name=db_session.bind.dialect.name,
        )
        assert [chunk.source_id for chunk in db_session.scalars(stmt)] == ["LOGIN"]

    def test_missing_metadata_never_matches(self, db_session) -> None:
        db_session.add_all(
            [
                _chunk("EMPTY"),
                _chunk("NULL", meta={"module": None}),
            ]
        )
        db_session.commit()

        stmt = apply_metadata_filters(
            select(KnowledgeChunk),
            {"module": "login"},
            dialect_name=db_session.bind.dialect.name,
        )
        assert list(db_session.scalars(stmt)) == []

    def test_array_filter_uses_contains_any(self, db_session) -> None:
        db_session.add_all(
            [
                _chunk("SMOKE", meta={"tags": ["login", "smoke"]}),
                _chunk("SECURITY", meta={"tags": ["security"]}),
                _chunk("MISSING"),
            ]
        )
        db_session.commit()

        stmt = apply_metadata_filters(
            select(KnowledgeChunk),
            {"tags": ["smoke", "security"]},
            dialect_name=db_session.bind.dialect.name,
        )
        assert [chunk.source_id for chunk in db_session.scalars(stmt)] == [
            "SMOKE",
            "SECURITY",
        ]

    def test_array_filter_matches_exactly_one_array_element(self, db_session) -> None:
        db_session.add_all(
            [
                _chunk("ONE", meta={"tags": ["login", "smoke"]}),
                _chunk("TWO", meta={"tags": ["security"]}),
            ]
        )
        db_session.commit()

        stmt = apply_metadata_filters(
            select(KnowledgeChunk),
            {"tags": ["pagination"]},
            dialect_name=db_session.bind.dialect.name,
        )
        assert list(db_session.scalars(stmt)) == []

        stmt = apply_metadata_filters(
            select(KnowledgeChunk),
            {"tags": ["smoke"]},
            dialect_name=db_session.bind.dialect.name,
        )
        assert [chunk.source_id for chunk in db_session.scalars(stmt)] == ["ONE"]

    def test_array_filter_does_not_match_scalar_or_missing(self, db_session) -> None:
        db_session.add_all(
            [
                _chunk("SCALAR", meta={"tags": "smoke"}),
                _chunk("MISSING"),
            ]
        )
        db_session.commit()

        stmt = apply_metadata_filters(
            select(KnowledgeChunk),
            {"tags": ["smoke"]},
            dialect_name=db_session.bind.dialect.name,
        )
        assert list(db_session.scalars(stmt)) == []

    def test_multiple_fields_are_combined_with_and(self, db_session) -> None:
        db_session.add_all(
            [
                _chunk("MATCH", meta={"module": "login", "priority": "P0"}),
                _chunk("WRONG_MODULE", meta={"module": "payment", "priority": "P0"}),
                _chunk("WRONG_PRIORITY", meta={"module": "login", "priority": "P1"}),
            ]
        )
        db_session.commit()

        stmt = apply_metadata_filters(
            select(KnowledgeChunk),
            {"module": "login", "priority": "P0"},
            dialect_name=db_session.bind.dialect.name,
        )
        assert [chunk.source_id for chunk in db_session.scalars(stmt)] == ["MATCH"]

    def test_related_cases_filter_uses_contains_any(self, db_session) -> None:
        db_session.add_all(
            [
                _chunk("BUG-1", source_type="defect", meta={"related_cases": ["TC-1"]}),
                _chunk("BUG-2", source_type="defect", meta={"related_cases": ["TC-2"]}),
            ]
        )
        db_session.commit()

        stmt = apply_metadata_filters(
            select(KnowledgeChunk),
            {"related_cases": ["TC-2", "TC-3"]},
            dialect_name=db_session.bind.dialect.name,
        )
        assert [chunk.source_id for chunk in db_session.scalars(stmt)] == ["BUG-2"]


class TestMetadataFilterJsonTypes:
    """数字 / 字符串 / 布尔在两种方言下都不能静默互配。"""

    def test_scalar_number_does_not_match_string_filter(self, db_session) -> None:
        db_session.add_all(
            [
                _chunk("NUMBER", meta={"priority": 1}),
                _chunk("BOOLEAN", meta={"priority": True}),
                _chunk("STRING", meta={"priority": "1"}),
                _chunk("FLOAT", meta={"priority": 1.0}),
            ]
        )
        db_session.commit()

        stmt = apply_metadata_filters(
            select(KnowledgeChunk),
            {"priority": "1"},
            dialect_name=db_session.bind.dialect.name,
        )

        assert [chunk.source_id for chunk in db_session.scalars(stmt)] == ["STRING"]

    def test_array_number_does_not_match_string_filter(self, db_session) -> None:
        db_session.add_all(
            [
                _chunk("NUMBER", meta={"tags": [1]}),
                _chunk("BOOLEAN", meta={"tags": [True]}),
                _chunk("STRING", meta={"tags": ["1"]}),
            ]
        )
        db_session.commit()

        stmt = apply_metadata_filters(
            select(KnowledgeChunk),
            {"tags": ["1", "security"]},
            dialect_name=db_session.bind.dialect.name,
        )

        assert [chunk.source_id for chunk in db_session.scalars(stmt)] == ["STRING"]

    def test_array_string_with_json_metacharacters_matches_exactly(self, db_session) -> None:
        db_session.add_all(
            [
                _chunk("UNICODE", meta={"tags": ['登录" 安全']}),
                _chunk("WRONG", meta={"tags": ["wrong"]}),
            ]
        )
        db_session.commit()

        stmt = apply_metadata_filters(
            select(KnowledgeChunk),
            {"tags": ['登录" 安全']},
            dialect_name=db_session.bind.dialect.name,
        )

        assert [chunk.source_id for chunk in db_session.scalars(stmt)] == ["UNICODE"]

    def test_non_array_container_does_not_match_array_filter(self, db_session) -> None:
        db_session.add_all(
            [
                _chunk("OBJECT", meta={"tags": {"smoke": True}}),
                _chunk("STRING", meta={"tags": "smoke"}),
                _chunk("ARRAY", meta={"tags": ["smoke"]}),
            ]
        )
        db_session.commit()

        stmt = apply_metadata_filters(
            select(KnowledgeChunk),
            {"tags": ["smoke"]},
            dialect_name=db_session.bind.dialect.name,
        )

        assert [chunk.source_id for chunk in db_session.scalars(stmt)] == ["ARRAY"]


def test_sqlite_scalar_predicate_has_json_type_guard() -> None:
    from sqlalchemy.dialects import sqlite

    stmt = apply_metadata_filters(
        select(KnowledgeChunk),
        {"priority": "1"},
        dialect_name="sqlite",
    )
    compiled = str(
        stmt.compile(
            dialect=sqlite.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "json_type" in compiled
    assert "text" in compiled
    assert "'$.priority'" in compiled


def test_filter_is_applied_before_limit(db_session) -> None:
    """候选饥饿回归：不能先取 Top-1 再过滤，否则目标 Chunk 永远拿不到。"""
    db_session.add_all(
        [
            _chunk("FIRST", meta={"module": "payment"}),
            _chunk("TARGET", meta={"module": "login"}),
        ]
    )
    db_session.commit()

    stmt = apply_metadata_filters(
        select(KnowledgeChunk).order_by(KnowledgeChunk.id).limit(1),
        {"module": "login"},
        dialect_name=db_session.bind.dialect.name,
    )
    assert [chunk.source_id for chunk in db_session.scalars(stmt)] == ["TARGET"]
