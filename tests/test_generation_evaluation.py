"""Phase 14 生成评测指标、Badcase 归因与候选 / 正式门槛测试。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.evaluation.generation_evaluation import (
    DeterministicGenerationEvaluator,
    GenerationCase,
    evaluate_generation,
    evaluate_generation_case,
    generation_cases_sha256,
)
from app.generation.rag import INSUFFICIENT_EVIDENCE
from app.schemas.rag import Citation, RAGResponse
from scripts import generation_evaluation

GOLD = ("document", "SHOP-DOC-002", 3)
CONTEXT = "超时后使用原幂等键查询交易状态。确认成功则补写本地支付记录并推进订单。"
GOLD_EVIDENCE = (*GOLD, "超时后使用原幂等键查询交易状态")
PHASE12 = Path("eval/fixtures/phase12_candidates.json")
PHASE14_RESPONSES = Path("eval/fixtures/phase14_simulated_responses.json")
PHASE6 = Path("eval/fixtures/phase6_simulated.json")


def _citation(rank: int = 1, *, source_id: str = "SHOP-DOC-002") -> Citation:
    return Citation(
        source_type="document",
        source_id=source_id,
        chunk_index=3,
        snippet=CONTEXT,
        rank=rank,
    )


def _response(
    answer: str,
    *,
    citations: list[Citation] | None = None,
    citation_valid: bool | None = None,
) -> RAGResponse:
    selected = [_citation()] if citations is None else citations
    return RAGResponse(
        question="支付网关超时后怎么办？",
        answer=answer,
        citation_valid=bool(selected) if citation_valid is None else citation_valid,
        citations=selected,
        retrieved_count=len(selected),
    )


def _case(
    *,
    query_id: str = "Q1",
    contexts: list[str] | None = None,
    points: list[str] | None = None,
    answerable: bool = True,
    gold_sources: list[tuple[str, str, int]] | None = None,
    gold_evidence: list[tuple[str, str, int, str]] | None = None,
    context_sources: list[tuple[str, str, int]] | None = None,
    review_status: str = "pending",
    reviewed_by: str | None = None,
    reviewed_at: datetime | None = None,
) -> GenerationCase:
    return GenerationCase(
        query_id=query_id,
        question="支付网关超时后怎么办？",
        contexts=[CONTEXT] if contexts is None else contexts,
        expected_answer_points=["使用原幂等键查询交易状态"] if points is None else points,
        answerable=answerable,
        gold_sources=[GOLD] if gold_sources is None else gold_sources,
        gold_evidence=(
            [GOLD_EVIDENCE]
            if gold_evidence is None and gold_sources is None
            else gold_evidence or []
        ),
        context_sources=[GOLD] if context_sources is None else context_sources,
        category="document_qa",
        review_status=review_status,  # type: ignore[arg-type]
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
    )


def test_correct_answer_with_valid_citation_has_no_badcase() -> None:
    detail = evaluate_generation_case(
        _case(),
        _response("应使用原幂等键查询交易状态。[1]"),
    )

    assert detail["badcase"] is None
    assert detail["metrics"]["faithfulness"] == 1.0
    assert detail["metrics"]["citation_accuracy"] == 1.0
    assert detail["metrics"]["context_recall"] == 1.0
    assert detail["metrics"]["context_precision"] == 1.0


def test_generation_error_when_answer_omits_expected_point() -> None:
    detail = evaluate_generation_case(
        _case(),
        _response("系统会自动发送短信。[1]"),
    )

    assert detail["badcase"] == "generation_error_missing_answer_point"
    assert detail["metrics"]["context_recall"] == 1.0
    assert detail["metrics"]["answer_coverage"] == 0.0


def test_generation_error_when_citation_number_is_out_of_range() -> None:
    detail = evaluate_generation_case(
        _case(),
        _response("应使用原幂等键查询交易状态。[2]", citations=[_citation(1)]),
    )

    assert detail["badcase"] == "generation_error_citation"
    assert detail["metrics"]["citation_accuracy"] == 0.0


def test_generation_error_when_citation_points_to_wrong_context_source() -> None:
    detail = evaluate_generation_case(
        _case(),
        _response(
            "应使用原幂等键查询交易状态。[1]",
            citations=[_citation(1, source_id="OTHER-DOC")],
        ),
    )

    assert detail["badcase"] == "generation_error_citation"
    assert detail["metrics"]["citation_accuracy"] == 0.0


def test_generation_error_when_answer_has_no_citation_for_factual_claim() -> None:
    detail = evaluate_generation_case(
        _case(),
        _response("应使用原幂等键查询交易状态。", citations=[], citation_valid=False),
    )

    assert detail["badcase"] == "generation_error_citation"
    assert detail["metrics"]["citation_accuracy"] == 0.0


def test_citation_accuracy_counts_duplicates_and_partial_invalid_numbers() -> None:
    citations = [_citation(1), _citation(2)]
    detail = evaluate_generation_case(
        _case(
            contexts=[CONTEXT, CONTEXT],
            context_sources=[GOLD, GOLD],
        ),
        _response(
            "应使用原幂等键查询交易状态。[1][2][3][1]",
            citations=citations,
        ),
    )

    assert detail["metrics"]["citation_accuracy"] == 0.75


def test_retrieval_error_when_context_has_no_gold_evidence() -> None:
    detail = evaluate_generation_case(
        _case(
            contexts=["这是一段无关的支付说明。"],
            context_sources=[("document", "OTHER", 0)],
        ),
        _response("可以先等待。[1]"),
    )

    assert detail["badcase"] == "retrieval_error"
    assert detail["metrics"]["context_recall"] == 0.0
    assert detail["metrics"]["context_precision"] == 0.0
    assert detail["metrics"]["faithfulness"] == 0.0


def test_retrieval_error_when_chunk_identity_matches_but_gold_evidence_is_absent() -> None:
    detail = evaluate_generation_case(
        _case(
            contexts=["完全无关的上下文。"],
            context_sources=[GOLD],
        ),
        _response("完全无关的上下文。[1]"),
    )

    assert detail["badcase"] == "retrieval_error"
    assert detail["metrics"]["context_recall"] == 0.0
    assert detail["metrics"]["context_precision"] == 0.0


def test_answerable_question_should_not_be_refused() -> None:
    detail = evaluate_generation_case(
        _case(
            contexts=[CONTEXT],
            context_sources=[GOLD],
        ),
        _response(INSUFFICIENT_EVIDENCE, citations=[], citation_valid=False),
    )

    assert detail["badcase"] == "generation_error_refusal"
    assert detail["metrics"]["refusal_correct"] is False
    assert detail["metrics"]["faithfulness"] == 1.0


def test_refusal_marker_in_mixed_answer_does_not_exempt_hallucination() -> None:
    detail = evaluate_generation_case(
        _case(
            contexts=["支付页面报错不等于扣款失败。"],
            points=["支付页面报错不等于扣款失败"],
            gold_evidence=[(*GOLD, "支付页面报错不等于扣款失败")],
        ),
        _response("支付页面报错不等于扣款失败。[1] 系统会自动退款。"),
    )

    assert detail["badcase"] == "generation_error_unsupported_claim"
    assert detail["metrics"]["faithfulness"] == 0.5
    assert detail["metrics"]["refusal_correct"] is True


def test_pure_refusal_with_reason_is_still_supported() -> None:
    detail = evaluate_generation_case(
        _case(
            contexts=["完全无关的上下文。"],
            context_sources=[("document", "OTHER", 0)],
            gold_evidence=[],
            points=[],
            answerable=False,
            gold_sources=[],
        ),
        _response("证据不足：当前知识库没有可用于回答该问题的内容。", citations=[]),
    )

    assert detail["metrics"]["faithfulness"] == 1.0
    assert detail["metrics"]["refusal_correct"] is True


def test_reference_answer_cannot_cover_multiple_independent_points() -> None:
    case = GenerationCase(
        query_id="Q1",
        question="问题",
        contexts=["要点一。"],
        expected_answer_points=["要点一", "要点二"],
        reference_answer="要点一",
        gold_sources=[GOLD],
        gold_evidence=[(*GOLD, "要点一")],
        context_sources=[GOLD],
    )
    detail = evaluate_generation_case(
        case,
        _response("要点一。[1]"),
    )

    assert detail["metrics"]["answer_coverage"] == 0.5
    assert detail["badcase"] == "generation_error_missing_answer_point"


def test_unanswerable_question_should_be_refused_and_hallucination_is_badcase() -> None:
    case = _case(
        contexts=[],
        points=[],
        answerable=False,
        gold_sources=[],
        context_sources=[],
    )
    refused = evaluate_generation_case(
        case,
        _response(INSUFFICIENT_EVIDENCE, citations=[], citation_valid=False),
    )
    answered = evaluate_generation_case(
        case,
        _response("系统会自动发送短信。[1]"),
    )

    assert refused["badcase"] is None
    assert refused["metrics"]["refusal_correct"] is True
    assert answered["badcase"] == "generation_error_false_answer"
    assert answered["metrics"]["refusal_correct"] is False


def test_unsupported_claim_is_distinguished_from_retrieval_error() -> None:
    detail = evaluate_generation_case(
        _case(),
        _response("应使用原幂等键查询交易状态，并且系统会自动发送短信。[1]"),
    )

    assert detail["badcase"] == "generation_error_unsupported_claim"
    assert detail["metrics"]["context_recall"] == 1.0
    assert detail["metrics"]["faithfulness"] < 1.0


def test_context_precision_is_unknown_without_source_metadata() -> None:
    metrics = DeterministicGenerationEvaluator().evaluate(
        question="支付超时怎么办？",
        answer="应使用原幂等键查询交易状态。",
        contexts=[CONTEXT],
        expected_answer_points=["使用原幂等键查询交易状态"],
        answerable=True,
    )

    assert metrics["context_precision"] is None
    assert metrics["context_recall"] is not None


def test_candidate_mode_allows_pending_and_aggregates_badcases() -> None:
    result = evaluate_generation(
        [_case(query_id="Q1"), _case(query_id="Q2")],
        [
            _response("应使用原幂等键查询交易状态。[1]"),
            _response("可以先等待。[1]"),
        ],
    )

    assert result.mode == "candidate"
    assert result.formal_gold_set is False
    assert result.metrics["cases"] == 2
    assert result.metrics["badcase_count"] == 1
    assert len(result.badcases) == 1
    assert result.metrics["unsupported_sentence_total"] == 1
    assert result.metrics["unsupported_sentence_rate"] == 0.5
    assert "unsupported_sentence_count" not in result.metrics


def test_formal_mode_rejects_pending_and_rejected_cases() -> None:
    with pytest.raises(ValueError, match="confirmed"):
        evaluate_generation([_case()], [_response("应使用原幂等键查询交易状态。[1]")], mode="formal")

    confirmed = _case(
        review_status="confirmed",
        reviewed_by="reviewer",
        reviewed_at=datetime(2026, 10, 22, tzinfo=UTC),
    )
    assert (
        evaluate_generation(
            [confirmed],
            [_response("应使用原幂等键查询交易状态。[1]")],
            mode="formal",
        ).formal_gold_set
        is True
    )

    rejected = _case(
        review_status="rejected",
        reviewed_by="reviewer",
        reviewed_at=datetime(2026, 10, 22, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="拒绝"):
        evaluate_generation([rejected], [_response("应使用原幂等键查询交易状态。[1]")])


def test_formal_mode_requires_expected_case_count() -> None:
    confirmed = _case(
        review_status="confirmed",
        reviewed_by="reviewer",
        reviewed_at=datetime(2026, 10, 22, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="必须覆盖 2 条"):
        evaluate_generation(
            [confirmed],
            [_response("应使用原幂等键查询交易状态。[1]")],
            mode="formal",
            expected_case_count=2,
        )


def test_case_contract_requires_review_provenance_and_matching_lengths() -> None:
    with pytest.raises(ValueError, match="reviewed_by"):
        _case(review_status="confirmed", reviewed_at=datetime(2026, 10, 22, tzinfo=UTC))
    with pytest.raises(ValueError, match="时区"):
        _case(
            review_status="confirmed",
            reviewed_by="reviewer",
            reviewed_at=datetime(2026, 10, 22),
        )
    with pytest.raises(ValueError, match="一一对应"):
        _case(context_sources=[GOLD, GOLD])


def test_batch_requires_matching_counts_and_unique_ids() -> None:
    with pytest.raises(ValueError, match="数量"):
        evaluate_generation([_case()], [])
    with pytest.raises(ValueError, match="至少"):
        evaluate_generation([], [])
    with pytest.raises(ValueError, match="query_id"):
        evaluate_generation(
            [_case(query_id="Q1"), _case(query_id="Q1")],
            [
                _response("应使用原幂等键查询交易状态。[1]"),
                _response("应使用原幂等键查询交易状态。[1]"),
            ],
        )


def test_case_hash_is_stable_and_changes_with_gold_data() -> None:
    first = _case()
    second = _case()
    changed = _case(points=["另一个答案要点"])

    assert generation_cases_sha256([first]) == generation_cases_sha256([second])
    assert generation_cases_sha256([first]) != generation_cases_sha256([changed])


def test_offline_fixture_builds_candidate_cases() -> None:
    cases, responses = generation_evaluation.build_cases(
        PHASE12,
        PHASE14_RESPONSES,
        PHASE6,
    )

    assert len(cases) == 10
    assert len(responses) == 10
    assert all(case.review_status == "pending" for case in cases)
    assert {case.query_id for case in cases} >= {"P12-DOC-007", "P12-UN-001"}


def test_response_fixture_rejects_unknown_query(tmp_path) -> None:
    payload = json.loads(PHASE14_RESPONSES.read_text(encoding="utf-8"))
    payload["responses"].append(
        {
            "query_id": "UNKNOWN",
            "answer": "不应被接受",
            "context_sources": [],
            "citations": [],
        }
    )
    path = tmp_path / "responses.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="未知 Query"):
        generation_evaluation.build_cases(PHASE12, path, PHASE6)


def test_cli_candidate_mode_writes_reproducible_manifest(tmp_path, monkeypatch) -> None:
    output = tmp_path / "phase14.json"
    monkeypatch.setattr(
        generation_evaluation.sys,
        "argv",
        [
            "generation_evaluation.py",
            "--dataset",
            str(PHASE12),
            "--responses",
            str(PHASE14_RESPONSES),
            "--corpus",
            str(PHASE6),
            "--output",
            str(output),
        ],
    )

    generation_evaluation.main()

    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["phase"] == 14
    assert manifest["mode"] == "candidate"
    assert manifest["formal_gold_set"] is False
    assert manifest["response_source"] == "simulated"
    assert manifest["dataset"]["hash_scope"] == "evaluated_cases"
    assert manifest["metrics"]["cases"] == 10


def test_cli_formal_mode_rejects_pending_without_output(tmp_path, monkeypatch, capsys) -> None:
    output = tmp_path / "formal.json"
    monkeypatch.setattr(
        generation_evaluation.sys,
        "argv",
        [
            "generation_evaluation.py",
            "--dataset",
            str(PHASE12),
            "--responses",
            str(PHASE14_RESPONSES),
            "--corpus",
            str(PHASE6),
            "--output",
            str(output),
            "--mode",
            "formal",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        generation_evaluation.main()

    assert exc_info.value.code == 1
    assert not output.exists()
    assert "仍有 100 条未确认" in capsys.readouterr().err
