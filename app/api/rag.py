"""Phase 3 最小 RAG API。"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from threading import BoundedSemaphore

from fastapi import APIRouter

from app.api.deps import SessionDep
from app.core.config import settings
from app.core.logging import get_logger
from app.generation.llm import LLMError, get_llm_client
from app.generation.rag import RAGService
from app.retrieval.embedding import get_embedding_client
from app.schemas.rag import RAGRequest, RAGResponse

router = APIRouter(prefix="/rag", tags=["rag"])
logger = get_logger(__name__)

#: 模块级共享线程池：请求之间复用，且 ``wait=False`` 的语义下超时不再阻塞当前请求。
#: 每请求新建 executor 时，``with`` 退出会 ``shutdown(wait=True)``，等于把工作时间
#: 完整算进请求耗时，「超时返回 503」就只是提前给了个响应。
_executor = ThreadPoolExecutor(
    max_workers=max(settings.rag_max_concurrency, 1),
    thread_name_prefix="rag-answer",
)
#: 并发闸门：满载时立刻返回 503，而不是让请求在线程池队列里无限堆积
_slots = BoundedSemaphore(max(settings.rag_max_concurrency, 1))


def _submit(payload: RAGRequest, session: SessionDep) -> Future[RAGResponse]:
    return _executor.submit(
        RAGService.answer,
        session,
        question=payload.question,
        embedding_client=get_embedding_client(),
        llm_factory=get_llm_client,
        top_k=payload.top_k,
        source_types=payload.source_types,
        max_distance=payload.max_distance,
    )


def _release_slot(future: Future[RAGResponse]) -> None:
    """任务结束（成功 / 失败 / 超时后跑完）时释放并发名额。

    超时路径不能立刻归还名额：工作线程仍在执行，必须等它真的结束，
    否则并发闸门会失去意义。同时消费掉异常，避免 Future 异常被静默丢弃。
    """
    error = future.exception() if not future.cancelled() else None  # noqa: SIM108
    if error is not None:
        logger.warning("RAG 后台任务失败：%s", error)
    try:
        _slots.release()
    except ValueError:  # pragma: no cover - 计数异常时不应让回调抛出
        logger.warning("RAG 并发名额释放异常，已忽略")


@router.post(
    "/answer",
    response_model=RAGResponse,
    summary="基于知识库回答问题（实验性慢接口）",
    description=(
        "同步执行 Vector Search + LLM。默认 OMLX 模型可能耗时 11–60 秒，"
        "超过 RAG_REQUEST_TIMEOUT 会返回 503；底层调用无法被中断（见 README 已知取舍），"
        "暂不建议用于交互式生产场景。"
    ),
)
def answer(
    payload: RAGRequest,
    session: SessionDep,
) -> RAGResponse:
    """实验性慢接口：真实推理模型可能耗时数十秒，超时或满载统一返回 503。

    已知取舍：超时只是放弃等待，任务仍会跑完（占用一个工作线程到 ``LLM_TIMEOUT``）。
    此时请求级数据库会话会被 FastAPI 依赖关闭，后台任务的数据库操作可能失败；
    异常由 ``_release_slot`` 记 warning，不影响已返回的响应。真正的解法是异步化
    （Phase 14/20），同步接口无法既中断调用又保持会话有效。
    """
    try:
        acquired = _slots.acquire(blocking=False)
    except ValueError:  # pragma: no cover - 信号量计数异常，视为满载
        acquired = False
    if not acquired:
        raise LLMError(
            "RAG 并发已满，请稍后重试",
            detail={"max_concurrency": settings.rag_max_concurrency},
        )

    future = _submit(payload, session)
    future.add_done_callback(_release_slot)
    try:
        return future.result(timeout=settings.rag_request_timeout)
    except FutureTimeoutError as exc:
        # 只放弃等待，不加 ``cancel``：运行在线程池里的调用无法被安全中断，
        # 但它不再占用当前请求（名额等任务真正结束后归还）。
        logger.warning(
            "RAG 请求超时 timeout=%s，底层调用仍在继续", settings.rag_request_timeout
        )
        raise LLMError(
            "RAG 请求超时",
            detail={"timeout_seconds": settings.rag_request_timeout},
        ) from exc


__all__ = ["router"]
