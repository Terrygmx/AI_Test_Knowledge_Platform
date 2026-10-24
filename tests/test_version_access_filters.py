"""Phase 22 / 23 SQL pre-filter 测试：版本区间与检索层权限。"""

from __future__ import annotations

import pytest

from app.models import KnowledgeChunk
from app.retrieval.embedding import content_sha256
from app.retrieval.versioning import VersionRange, parse_version, version_key
from app.services.knowledge_chunk_service import KnowledgeChunkService


def test_version_key_zero_pads_for_sorting() -> None:
    assert version_key("v2.10") == "0002.0010.0000"
    assert version_key("v2.9") == "0002.0009.0000"
    assert version_key("v2.10") > version_key("v2.9")
    assert version_key("V1.2.3") == "0001.0002.0003"
    assert version_key(None) is None
    assert version_key("2.9") is None
    assert version_key("unknown") is None


def test_version_range_still_rejects_invalid_bounds() -> None:
    with pytest.raises(ValueError, match="版本号"):
        VersionRange(min_version="2.9")


def _chunk(source_id: str, *, version: str | None = None, meta: dict | None = None) -> KnowledgeChunk:
    return KnowledgeChunk(
        source_type="testcase",
        source_id=source_id,
        chunk_index=0,
        content=source_id,
        content_hash=content_sha256(source_id),
        version=version,
        version_key=version_key(version),
        meta=meta or {},
    )


class TestVersionRangeSQL:
    def test_range_filters_numerically_and_excludes_missing(self, db_session) -> None:
        db_session.add_all(
            [
                _chunk("V29", version="v2.9"),
                _chunk("V210", version="v2.10"),
                _chunk("V24", version="v2.4"),
                _chunk("NOVER"),
            ]
        )
        db_session.commit()

        hits = KnowledgeChunkService.lexical_search(
            db_session,
            query="V210",
            limit=10,
            version_range=VersionRange(min_version="v2.10", max_version="v2.10"),
        )

        assert [chunk.source_id for chunk, _, _ in hits] == ["V210"]

    def test_range_without_bounds_returns_all(self, db_session) -> None:
        db_session.add_all([_chunk("A", version="v1.0"), _chunk("B")])
        db_session.commit()

        hits = KnowledgeChunkService.lexical_search(
            db_session,
            query="",
            limit=10,
        )

        assert hits == []


class TestAccessFilter:
    CTX = {"team": "qa", "project": "shop", "role_rank": 0}  # viewer

    def test_missing_access_is_visible_to_everyone(self, db_session) -> None:
        db_session.add(_chunk("OPEN"))
        db_session.commit()

        hits = KnowledgeChunkService.lexical_search(
            db_session,
            query="OPEN",
            limit=10,
            access_context=self.CTX,
        )

        assert [chunk.source_id for chunk, _, _ in hits] == ["OPEN"]

    def test_team_mismatch_is_hidden(self, db_session) -> None:
        db_session.add(_chunk("OTHER", meta={"access": {"teams": ["platform"]}}))
        db_session.commit()

        hits = KnowledgeChunkService.lexical_search(
            db_session,
            query="SECRET",
            limit=10,
            access_context=self.CTX,
        )

        assert hits == []

    def test_role_below_min_role_is_hidden(self, db_session) -> None:
        db_session.add(_chunk("SECRET", meta={"access": {"min_role": "editor"}}))
        db_session.commit()

        hits = KnowledgeChunkService.lexical_search(
            db_session,
            query="SECRET",
            limit=10,
            access_context=self.CTX,
        )

        assert hits == []

    def test_editor_role_passes_min_role_editor(self, db_session) -> None:
        db_session.add(_chunk("SECRET", meta={"access": {"min_role": "editor"}}))
        db_session.commit()

        hits = KnowledgeChunkService.lexical_search(
            db_session,
            query="SECRET",
            limit=10,
            access_context={"team": "qa", "project": "shop", "role_rank": 1},
        )

        assert [chunk.source_id for chunk, _, _ in hits] == ["SECRET"]

    def test_malformed_access_is_hidden_even_for_admin(self, db_session) -> None:
        db_session.add_all(
            [
                _chunk("BAD-TYPE", meta={"access": "public"}),
                _chunk("BAD-ROLE", meta={"access": {"min_role": "owner"}}),
            ]
        )
        db_session.commit()

        hits = KnowledgeChunkService.lexical_search(
            db_session,
            query="BAD",
            limit=10,
            access_context={"team": "qa", "project": "shop", "role_rank": 2},
        )

        assert hits == []

    def test_no_access_context_disables_filtering(self, db_session) -> None:
        db_session.add(_chunk("SECRET", meta={"access": {"min_role": "admin"}}))
        db_session.commit()

        hits = KnowledgeChunkService.lexical_search(
            db_session,
            query="SECRET",
            limit=10,
        )

        assert len(hits) == 1


def test_parse_version_still_works() -> None:
    assert parse_version("v3.1.4") == (3, 1, 4)
