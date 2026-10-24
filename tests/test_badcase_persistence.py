"""Phase 19 Badcase 持久化测试：服务层 + API + 迁移后表可用。"""

from __future__ import annotations

import pytest

from app.core.exceptions import DuplicateError, NotFoundError
from app.evaluation.badcase import Badcase, BadcaseStage, BadcaseStatus
from app.services.badcase_service import BadcaseService


def _badcase(badcase_id: str = "bc-001") -> Badcase:
    return Badcase(
        badcase_id=badcase_id,
        query_id="q-001",
        stage=BadcaseStage.RETRIEVAL,
        symptom="gold chunk 未进入 top-10",
        root_cause="BM25 分词未保留标识符",
        hypothesis="分词表补充标识符规则",
        regression_query_ids=["q-001"],
    )


def test_create_get_and_duplicate_guard(db_session) -> None:
    BadcaseService.create(db_session, _badcase())
    loaded = BadcaseService.get(db_session, "bc-001")
    assert loaded.stage is BadcaseStage.RETRIEVAL
    assert loaded.status is BadcaseStatus.OPEN

    with pytest.raises(DuplicateError):
        BadcaseService.create(db_session, _badcase())


def test_advance_persists_status_and_eval_result(db_session) -> None:
    badcase = _badcase()
    BadcaseService.create(db_session, badcase)

    badcase.advance()
    badcase.advance()
    badcase.advance(evaluation_result={"recall@5": 1.0})
    BadcaseService.save(db_session, badcase)

    loaded = BadcaseService.get(db_session, "bc-001")
    assert loaded.status is BadcaseStatus.VERIFIED
    assert loaded.evaluation_result == {"recall@5": 1.0}


def test_list_filters_by_stage_and_status(db_session) -> None:
    other = _badcase("bc-002")
    other.stage = BadcaseStage.GENERATION
    BadcaseService.create(db_session, _badcase())
    BadcaseService.create(db_session, other)

    assert [b.badcase_id for b in BadcaseService.list(db_session)] == ["bc-001", "bc-002"]
    assert [b.badcase_id for b in BadcaseService.list(db_session, stage=BadcaseStage.GENERATION)] == [
        "bc-002"
    ]
    assert len(BadcaseService.list(db_session, status=BadcaseStatus.OPEN)) == 2


def test_get_missing_raises(db_session) -> None:
    with pytest.raises(NotFoundError):
        BadcaseService.get(db_session, "missing")


def test_advance_verified_requires_evaluation_result(db_session) -> None:
    badcase = _badcase()
    BadcaseService.create(db_session, badcase)
    badcase.advance()
    badcase.advance()
    with pytest.raises(ValueError, match="evaluation_result"):
        badcase.advance()


class TestBadcaseAPI:
    API = "/api/v1/badcases"

    def test_create_list_get_advance(self, client) -> None:
        payload = {
            "badcase_id": "bc-api-1",
            "query_id": "q-001",
            "stage": "retrieval",
            "symptom": "gold 未命中",
            "root_cause": "过滤条件过严",
            "hypothesis": "放宽 filter",
            "regression_query_ids": ["q-001"],
        }
        response = client.post(self.API, json=payload)
        assert response.status_code == 201
        assert response.json()["status"] == "open"

        response = client.post(
            f"{self.API}/bc-api-1/advance",
            json={"evaluation_result": None},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "fixing"

        response = client.get(self.API, params={"stage": "retrieval"})
        assert response.status_code == 200
        assert [item["badcase_id"] for item in response.json()] == ["bc-api-1"]

        response = client.get(f"{self.API}/bc-api-1")
        assert response.status_code == 200
        assert response.json()["hypothesis"] == "放宽 filter"

    def test_advance_to_verified_requires_evaluation_result(self, client) -> None:
        payload = {
            "badcase_id": "bc-api-2",
            "query_id": "q-002",
            "stage": "generation",
            "symptom": "答案缺要点",
            "root_cause": "prompt 未强调要点",
            "hypothesis": "调整 prompt",
            "regression_query_ids": ["q-002"],
        }
        client.post(self.API, json=payload)
        client.post(f"{self.API}/bc-api-2/advance")
        client.post(f"{self.API}/bc-api-2/advance")
        response = client.post(f"{self.API}/bc-api-2/advance", json={"evaluation_result": None})
        assert response.status_code == 400

    def test_duplicate_returns_409(self, client) -> None:
        payload = {
            "badcase_id": "bc-api-dup",
            "query_id": "q-003",
            "stage": "retrieval",
            "symptom": "s",
            "root_cause": "r",
            "hypothesis": "h",
            "regression_query_ids": ["q-003"],
        }
        assert client.post(self.API, json=payload).status_code == 201
        response = client.post(self.API, json=payload)
        assert response.status_code == 409
