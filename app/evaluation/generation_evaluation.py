"""Phase 14 生成评测的可解释核心。

这里先实现不依赖框架的确定性检查，再保留可选 Ragas 适配入口：

- Context Recall：Gold Chunk 身份是否被上下文召回；无来源元数据时退化为答案要点覆盖；
- Context Precision：进入上下文的证据块中有多少与 Gold 来源匹配；
- Faithfulness：答案中的事实句是否能从上下文找到支撑；
- Answer Relevancy：答案是否覆盖问题与预期答案要点；
- Citation Accuracy：引用是否指向本次上下文中的真实来源；
- 拒答一致性：不可回答问题是否明确拒答、可回答问题是否没有误拒答。

这些指标不是 LLM-as-a-judge 的替代品。它们适合回归测试和 Badcase 初筛，
正式生成质量仍需人工确认集与可选 Ragas 评测。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

from app.evaluation.metrics import normalize_text
from app.generation.rag import INSUFFICIENT_EVIDENCE
from app.schemas.rag import Citation, RAGResponse

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])\s*|\n+")
_TOKEN_SPLIT = re.compile(r"[\s,，。；;、:：()（）\[\]【】\"'“”‘’]+")
_CITATION_MARK = re.compile(r"\[\s*(\d+(?:\s*[,，]\s*\d+)*)\s*\]")
_REFUSAL_MARKERS = ("证据不足", "无法回答", "无法确认", "没有可用于回答", "未找到相关")
_REFUSAL_ONLY = re.compile(
    r"^(?:证据不足|无法回答|无法确认|没有可用于回答|未找到相关)"
    r"(?:[：:，,。；;！!？?\s]|当前|暂时|相关|该|此|其他|进一步|更多|"
    r"知识库|没有|可|用于|回答|问题|的|信息|内容|答案|依据|证据|无法|未|不能|不足)*$"
)


class GenerationEvaluator(Protocol):
    """可替换的生成评测器；默认实现不调用外部 LLM。"""

    name: str

    def evaluate(
        self,
        *,
        question: str,
        answer: str,
        contexts: Sequence[str],
        expected_answer_points: Sequence[str],
        answerable: bool,
        citations: Sequence[Citation] = (),
        gold_sources: Sequence[tuple[str, str, int]] = (),
        gold_evidence: Sequence[tuple[str, str, int, str]] = (),
        context_sources: Sequence[tuple[str, str, int]] = (),
        reference_answer: str | None = None,
    ) -> dict[str, float | bool | str | None]: ...


@dataclass(frozen=True)
class GenerationCase:
    """一条生成评测样本。"""

    query_id: str
    question: str
    contexts: list[str]
    expected_answer_points: list[str]
    answerable: bool = True
    gold_sources: list[tuple[str, str, int]] = field(default_factory=list)
    gold_evidence: list[tuple[str, str, int, str]] = field(default_factory=list)
    context_sources: list[tuple[str, str, int]] = field(default_factory=list)
    reference_answer: str | None = None
    category: str = ""
    review_status: Literal["pending", "confirmed", "rejected"] = "pending"
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.review_status not in {"pending", "confirmed", "rejected"}:
            raise ValueError("review_status 必须是 pending / confirmed / rejected")
        if self.review_status == "pending":
            if self.reviewed_by is not None or self.reviewed_at is not None:
                raise ValueError("pending 样本不能带审阅人 / 审阅时间")
        else:
            if not self.reviewed_by:
                raise ValueError(f"{self.review_status} 样本必须记录 reviewed_by")
            if self.reviewed_at is None:
                raise ValueError(f"{self.review_status} 样本必须记录 reviewed_at")
            if self.reviewed_at.tzinfo is None or self.reviewed_at.utcoffset() is None:
                raise ValueError("reviewed_at 必须带时区")
        if self.context_sources and len(self.context_sources) != len(self.contexts):
            raise ValueError("context_sources 必须与 contexts 一一对应")
        gold_keys = set(self.gold_sources)
        for source_type, source_id, chunk_index, evidence in self.gold_evidence:
            if (source_type, source_id, chunk_index) not in gold_keys:
                raise ValueError("gold_evidence 必须对应 gold_sources 中的 Chunk")
            if not evidence.strip():
                raise ValueError("gold_evidence 不能包含空白证据")
        if self.answerable and not self.expected_answer_points:
            raise ValueError("可回答样本必须提供 expected_answer_points")
        if not self.answerable and self.expected_answer_points:
            raise ValueError("不可回答样本不应提供 expected_answer_points")
        if (
            self.answerable
            and self.reference_answer is not None
            and not self.reference_answer.strip()
        ):
            raise ValueError("reference_answer 不能为空白")


@dataclass
class GenerationEvaluationResult:
    """生成评测的总体、分类与逐样本结果。"""

    mode: str
    evaluator: str
    dataset_sha256: str
    formal_gold_set: bool
    metrics: dict[str, float | int | None | dict[str, int]]
    cases: list[dict[str, Any]]
    badcases: list[dict[str, Any]]


def _tokens(text: str) -> set[str]:
    normalized = normalize_text(text)
    return {token for token in _TOKEN_SPLIT.split(normalized) if len(token) >= 2 or token.isdigit()}


def _char_ngrams(text: str, size: int = 2) -> set[str]:
    compact = re.sub(r"\s+", "", normalize_text(text))
    if len(compact) < size:
        return {compact} if compact else set()
    return {compact[index : index + size] for index in range(len(compact) - size + 1)}


def _sentences(answer: str) -> list[str]:
    """只返回包含文字的句子；独立引用编号不是事实句。"""
    return [
        item.strip()
        for item in _SENTENCE_SPLIT.split(answer)
        if _CITATION_MARK.sub("", item).strip()
    ]


def _contains_any(text: str, needles: Iterable[str]) -> bool:
    normalized = normalize_text(text)
    return any(normalize_text(needle) in normalized for needle in needles if needle.strip())


def _context_supported(sentence: str, contexts: Sequence[str]) -> bool:
    """事实句需要原文或足够高的字符片段重合；只有纯拒答句视为受支持。"""
    stripped = _CITATION_MARK.sub("", sentence).strip()
    if not stripped:
        return True
    if _is_pure_refusal(stripped):
        return True
    normalized_sentence = normalize_text(stripped)
    normalized_context = normalize_text("\n".join(contexts))
    if normalized_sentence and normalized_sentence in normalized_context:
        return True

    sentence_grams = _char_ngrams(stripped)
    context_grams = _char_ngrams("\n".join(contexts))
    if not sentence_grams or not context_grams:
        return False
    return len(sentence_grams & context_grams) / len(sentence_grams) >= 0.6


def _covers_any(text: str, candidates: Sequence[str], *, threshold: float = 0.45) -> bool:
    """判断文本是否覆盖某个要点；允许同义改写，但仍保持确定性。"""
    normalized = normalize_text(text)
    for candidate in candidates:
        needle = normalize_text(candidate)
        if needle and needle in normalized:
            return True
        needle_grams = _char_ngrams(candidate)
        text_grams = _char_ngrams(text)
        if (
            len(needle_grams) >= 4
            and len(needle_grams & text_grams) / len(needle_grams) >= threshold
        ):
            return True
    return False


def _reference_match(text: str, reference_answer: str) -> bool:
    """参考答案是生成评测的语义锚点，采用更保守的字符覆盖判断。"""
    compact_text = re.sub(r"\s+", "", normalize_text(text))
    compact_reference = re.sub(r"\s+", "", normalize_text(reference_answer))
    needle_grams = _char_ngrams(compact_reference)
    text_grams = _char_ngrams(compact_text)
    if not needle_grams or not text_grams:
        return False
    return len(needle_grams & text_grams) / len(needle_grams) >= 0.55


def _is_refusal(answer: str) -> bool:
    normalized = normalize_text(answer)
    return any(marker in normalized for marker in _REFUSAL_MARKERS)


def _is_pure_refusal(answer: str) -> bool:
    """只把没有事实断言的短拒答句视为拒答。"""
    normalized = normalize_text(_CITATION_MARK.sub("", answer)).strip()
    if not normalized or not _is_refusal(normalized):
        return False
    if len(normalized) > 60:
        return False
    sentences = _sentences(normalized)
    return len(sentences) == 1 and bool(_REFUSAL_ONLY.fullmatch(sentences[0]))


def _citation_numbers(answer: str) -> list[int]:
    numbers: list[int] = []
    for match in _CITATION_MARK.finditer(answer):
        numbers.extend(int(item) for item in re.split(r"\s*[,，]\s*", match.group(1)))
    return numbers


def _citation_accuracy(
    answer: str,
    citations: Sequence[Citation],
    *,
    available_contexts: int,
    context_sources: Sequence[tuple[str, str, int]],
    refusal: bool,
) -> float:
    """合法引用编号数 / 答案引用编号总数。

    引用编号既要在本次上下文的编号范围内，也必须能对应到响应中真实返回的
    ``Citation.rank``。重复引用逐次计分，越界或没有对应 Citation 的编号计错。
    明确拒答不需要引用；事实回答没有任何引用时计 0，不能把“未引用”当作准确。
    """
    numbers = _citation_numbers(answer)
    if not numbers:
        return 1.0 if refusal else 0.0

    returned_ranks: set[int] = set()
    for item in citations:
        if not 1 <= item.rank <= available_contexts:
            continue
        citation_key = (item.source_type, item.source_id, item.chunk_index)
        if context_sources and citation_key != context_sources[item.rank - 1]:
            continue
        returned_ranks.add(item.rank)
    valid = sum(
        1
        for number in numbers
        if 1 <= number <= available_contexts and number in returned_ranks
    )
    return valid / len(numbers)


def _context_precision(
    context_sources: Sequence[tuple[str, str, int]],
    gold_sources: Sequence[tuple[str, str, int]],
) -> float | None:
    """上下文块中匹配 Gold Chunk 身份的比例；缺少来源元数据时不计算。"""
    if not context_sources or not gold_sources:
        return None
    gold = set(gold_sources)
    return sum(source in gold for source in context_sources) / len(context_sources)


def _evidence_hit(text: str, evidence: str) -> bool:
    normalized_evidence = normalize_text(evidence)
    return bool(normalized_evidence) and normalized_evidence in normalize_text(text)


def _context_evidence_recall(
    contexts: Sequence[str],
    context_sources: Sequence[tuple[str, str, int]],
    gold_evidence: Sequence[tuple[str, str, int, str]],
) -> float | None:
    """同时按 Chunk 身份和正文证据校验上下文召回。"""
    if not contexts or not context_sources or not gold_evidence:
        return None
    by_source: dict[tuple[str, str, int], list[str]] = {}
    for source_type, source_id, chunk_index, evidence in gold_evidence:
        by_source.setdefault((source_type, source_id, chunk_index), []).append(evidence)
    hits = 0
    for index, source in enumerate(context_sources):
        text = contexts[index] if index < len(contexts) else ""
        evidences = by_source.get(source, [])
        if evidences and all(_evidence_hit(text, evidence) for evidence in evidences):
            hits += 1
    return hits / len(by_source)


def _context_evidence_precision(
    contexts: Sequence[str],
    context_sources: Sequence[tuple[str, str, int]],
    gold_evidence: Sequence[tuple[str, str, int, str]],
) -> float | None:
    """上下文块必须身份匹配且包含 Gold 证据，才算精确命中。"""
    if not contexts or not context_sources or not gold_evidence:
        return None
    by_source: dict[tuple[str, str, int], list[str]] = {}
    for source_type, source_id, chunk_index, evidence in gold_evidence:
        by_source.setdefault((source_type, source_id, chunk_index), []).append(evidence)
    hits = 0
    for index, source in enumerate(context_sources):
        text = contexts[index] if index < len(contexts) else ""
        evidences = by_source.get(source, [])
        if evidences and all(_evidence_hit(text, evidence) for evidence in evidences):
            hits += 1
    return hits / len(context_sources)


def _context_recall_by_source(
    context_sources: Sequence[tuple[str, str, int]],
    gold_sources: Sequence[tuple[str, str, int]],
) -> float | None:
    """Gold Chunk 身份被上下文召回的比例；缺少来源元数据时不计算。"""
    if not context_sources or not gold_sources:
        return None
    return len(set(gold_sources) & set(context_sources)) / len(set(gold_sources))


class DeterministicGenerationEvaluator:
    """无需模型服务的确定性生成评测器。

    这是低成本的回归基线，不声称等价于 Ragas 的语义判断。它尤其适合验证：

    - 检索正确但答案引用错误；
    - 检索正确但答案遗漏预期要点；
    - 不可回答问题被误答；
    - 答案包含上下文中没有的事实句。
    """

    name = "deterministic"

    def evaluate(
        self,
        *,
        question: str,
        answer: str,
        contexts: Sequence[str],
        expected_answer_points: Sequence[str],
        answerable: bool,
        citations: Sequence[Citation] = (),
        gold_sources: Sequence[tuple[str, str, int]] = (),
        gold_evidence: Sequence[tuple[str, str, int, str]] = (),
        context_sources: Sequence[tuple[str, str, int]] = (),
        reference_answer: str | None = None,
    ) -> dict[str, float | bool | str | None]:
        if not answerable:
            expected_answer_points = []
        context_text = "\n".join(contexts)
        reference_points = (
            [reference_answer] if reference_answer is not None else expected_answer_points
        )
        point_hits = [
            point
            for point in reference_points
            if _contains_any(context_text, [point])
            or _covers_any(context_text, [point])
            or (
                reference_answer is not None
                and _reference_match(context_text, reference_answer)
            )
        ]
        answer_point_hits = [
            point
            for point in expected_answer_points
            if _contains_any(answer, [point]) or _covers_any(answer, [point])
        ]
        if (
            reference_answer is not None
            and len(expected_answer_points) == 1
            and _reference_match(answer, reference_answer)
        ):
            # 多要点样本必须逐点匹配；参考答案不能替代多个独立要点的覆盖判断。
            answer_point_hits = list(expected_answer_points)
        context_recall = (
            len(point_hits) / len(reference_points) if reference_points else None
        )
        source_context_recall = _context_recall_by_source(context_sources, gold_sources)
        evidence_context_recall = _context_evidence_recall(
            contexts,
            context_sources,
            gold_evidence,
        )
        if evidence_context_recall is not None:
            context_recall = evidence_context_recall
        elif source_context_recall is not None:
            context_recall = source_context_recall
        answer_coverage = (
            len(answer_point_hits) / len(expected_answer_points)
            if expected_answer_points
            else None
        )

        sentences = _sentences(answer)
        supported = [sentence for sentence in sentences if _context_supported(sentence, contexts)]
        faithfulness = len(supported) / len(sentences) if sentences else 0.0
        unsupported_sentences = [
            sentence
            for sentence in sentences
            if not _context_supported(sentence, contexts)
        ]
        refusal = _is_pure_refusal(answer)
        if refusal and answer == INSUFFICIENT_EVIDENCE:
            # 无上下文时的系统拒答本身是可靠行为，不是事实句。
            faithfulness = 1.0

        question_tokens = _tokens(question)
        answer_tokens = _tokens(answer)
        relevance = (
            len(question_tokens & answer_tokens) / len(question_tokens) if question_tokens else 0.0
        )
        answer_relevancy = 0.5 * relevance + 0.5 * (
            answer_coverage if answer_coverage is not None else 0.0
        )

        available_contexts = len(context_sources) if context_sources else len(contexts)
        citation_accuracy = _citation_accuracy(
            answer,
            citations,
            available_contexts=available_contexts,
            context_sources=context_sources,
            refusal=refusal,
        )
        refusal_correct = refusal if not answerable else not refusal
        return {
            "context_recall": context_recall,
            "context_precision": (
                _context_evidence_precision(contexts, context_sources, gold_evidence)
                if gold_evidence
                else _context_precision(context_sources, gold_sources)
            ),
            "faithfulness": faithfulness,
            "answer_relevancy_heuristic": answer_relevancy,
            "answer_relevancy": answer_relevancy,
            "answer_coverage": answer_coverage,
            "citation_accuracy": citation_accuracy,
            "refusal_correct": refusal_correct,
            "unsupported_sentence_count": len(unsupported_sentences),
        }


def _aggregate(
    results: Sequence[dict[str, Any]],
    keys: Sequence[str],
) -> dict[str, float | int | None | dict[str, int]]:
    summary: dict[str, float | int | None | dict[str, int]] = {
        "cases": len(results),
        "badcase_count": sum(item["badcase"] is not None for item in results),
    }
    metric_case_counts: dict[str, int] = {}
    for key in keys:
        values = [
            float(item["metrics"][key])
            for item in results
            if item["metrics"][key] is not None
        ]
        metric_case_counts[key] = len(values)
        summary[key] = sum(values) / len(values) if values else None
    summary["metric_case_counts"] = metric_case_counts
    return summary


def generation_cases_sha256(cases: Sequence[GenerationCase]) -> str:
    """对样本与 Gold 契约做稳定哈希；不包含评测结果或环境噪声。"""
    payload = [
        {
            "query_id": case.query_id,
            "question": case.question,
            "contexts": list(case.contexts),
            "context_sources": [list(item) for item in case.context_sources],
            "expected_answer_points": list(case.expected_answer_points),
            "answerable": case.answerable,
            "gold_sources": [list(item) for item in case.gold_sources],
            "gold_evidence": [list(item) for item in case.gold_evidence],
            "reference_answer": case.reference_answer,
            "category": case.category,
            "review_status": case.review_status,
            "reviewed_by": case.reviewed_by,
            "reviewed_at": case.reviewed_at.isoformat() if case.reviewed_at else None,
        }
        for case in cases
    ]
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def evaluate_generation_case(
    case: GenerationCase,
    response: RAGResponse,
    *,
    evaluator: GenerationEvaluator | None = None,
) -> dict[str, Any]:
    """评测一条生成结果，并给出阶段化 Badcase。"""
    active = evaluator or DeterministicGenerationEvaluator()
    metrics = active.evaluate(
        question=case.question,
        answer=response.answer,
        contexts=case.contexts,
        expected_answer_points=case.expected_answer_points,
        answerable=case.answerable,
        citations=response.citations,
        gold_sources=case.gold_sources,
        gold_evidence=case.gold_evidence,
        context_sources=case.context_sources,
        reference_answer=case.reference_answer,
    )
    badcase: str | None = None
    context_recall = metrics["context_recall"]
    context_precision = metrics["context_precision"]
    if case.answerable and (
        (context_recall is not None and context_recall < 1.0)
        or context_precision == 0.0
    ):
        badcase = "retrieval_error"
    elif case.answerable and not metrics["refusal_correct"]:
        badcase = "generation_error_refusal"
    elif not case.answerable and not metrics["refusal_correct"]:
        badcase = "generation_error_false_answer"
    elif (
        case.answerable
        and (
            (metrics["answer_coverage"] is not None and metrics["answer_coverage"] < 1.0)
            or (metrics["context_recall"] is not None and metrics["context_recall"] < 1.0)
        )
    ):
        badcase = "generation_error_missing_answer_point"
    elif case.answerable and metrics["citation_accuracy"] < 1.0:
        badcase = "generation_error_citation"
    elif case.answerable and metrics["faithfulness"] < 1.0:
        badcase = "generation_error_unsupported_claim"
    elif case.answerable and not _is_refusal(response.answer) and response.citation_valid is False:
        badcase = "generation_error_citation"

    return {
        "query_id": case.query_id,
        "category": case.category,
        "answerable": case.answerable,
        "review_status": case.review_status,
        "metrics": metrics,
        "badcase": badcase,
        "answer": response.answer,
        "citations": [item.model_dump(mode="json") for item in response.citations],
    }


def evaluate_generation(
    cases: Sequence[GenerationCase],
    responses: Sequence[RAGResponse],
    *,
    mode: Literal["candidate", "formal"] = "candidate",
    expected_case_count: int | None = None,
    evaluator: GenerationEvaluator | None = None,
) -> GenerationEvaluationResult:
    """批量评测生成结果，并输出总体、分类与 Badcase。"""
    if mode not in {"candidate", "formal"}:
        raise ValueError("mode 必须是 candidate 或 formal")
    if len(cases) != len(responses):
        raise ValueError("cases 与 responses 数量必须一致")
    rejected = [case.query_id for case in cases if case.review_status == "rejected"]
    if rejected:
        preview = ", ".join(rejected[:5])
        suffix = "..." if len(rejected) > 5 else ""
        raise ValueError(f"已拒绝样本不能参与评测：{preview}{suffix}")
    if mode == "formal":
        not_confirmed = [
            case.query_id for case in cases if case.review_status != "confirmed"
        ]
        if not_confirmed:
            preview = ", ".join(not_confirmed[:5])
            suffix = "..." if len(not_confirmed) > 5 else ""
            raise ValueError(
                f"formal 模式要求全部样本为 confirmed；仍有 {len(not_confirmed)} 条未确认："
                f"{preview}{suffix}"
            )
    if not cases:
        raise ValueError("至少需要一条生成评测样本")
    if expected_case_count is not None and len(cases) != expected_case_count:
        raise ValueError(
            f"数据集必须覆盖 {expected_case_count} 条样本；实际为 {len(cases)}"
        )
    case_ids = [case.query_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("query_id 不能重复")
    active = evaluator or DeterministicGenerationEvaluator()
    details = [
        evaluate_generation_case(case, response, evaluator=active)
        for case, response in zip(cases, responses, strict=True)
    ]
    metric_keys = (
        "context_recall",
        "context_precision",
        "faithfulness",
        "answer_relevancy_heuristic",
        "answer_relevancy",
        "answer_coverage",
        "citation_accuracy",
        "refusal_correct",
        "unsupported_sentence_count",
    )
    categories: dict[str, dict[str, float | int | None | dict[str, int]]] = {}
    for category in sorted({case.category for case in cases}):
        subset = [item for item in details if item["category"] == category]
        categories[category] = _aggregate(subset, metric_keys)
    unsupported_sentence_total = sum(
        int(item["metrics"]["unsupported_sentence_count"]) for item in details
    )
    summary = _aggregate(details, metric_keys)
    summary["unsupported_sentence_total"] = unsupported_sentence_total
    summary["unsupported_sentence_rate"] = (
        unsupported_sentence_total / len(details) if details else 0.0
    )
    metric_case_counts = summary.get("metric_case_counts")
    if isinstance(metric_case_counts, dict):
        metric_case_counts["unsupported_sentence_total"] = len(details)
        metric_case_counts["unsupported_sentence_rate"] = len(details)
        metric_case_counts.pop("unsupported_sentence_count", None)
    summary.pop("unsupported_sentence_count", None)

    return GenerationEvaluationResult(
        mode=mode,
        evaluator=active.name,
        dataset_sha256=generation_cases_sha256(cases),
        formal_gold_set=mode == "formal",
        metrics=summary,
        cases=details,
        badcases=[item for item in details if item["badcase"]],
    )


class RagasEvaluator:
    """可选 Ragas 适配器。

    Ragas 不作为默认依赖；只有安装后才可构造。当前只提供最小接口，
    避免在未固定版本、未确认模型和指标参数时伪造正式分数。
    """

    name = "ragas"

    def __init__(self) -> None:
        try:
            import ragas  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "Ragas 未安装；请先在隔离环境安装并固定版本，再启用正式生成评测"
            ) from exc

    def evaluate(self, **kwargs: Any) -> dict[str, float | bool | str | None]:
        raise NotImplementedError("Ragas 指标参数尚未固定；当前只开放确定性评测器")


__all__ = [
    "DeterministicGenerationEvaluator",
    "GenerationCase",
    "GenerationEvaluationResult",
    "GenerationEvaluator",
    "RagasEvaluator",
    "evaluate_generation",
    "evaluate_generation_case",
    "generation_cases_sha256",
]
