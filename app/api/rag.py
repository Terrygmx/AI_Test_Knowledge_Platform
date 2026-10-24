"""Phase 3 最小 RAG API。"""

from __future__ import annotations

from collections.abc import Callable
from threading import BoundedSemaphore, Event, Thread
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.exceptions import DomainError
from app.core.logging import get_logger
from app.generation.llm import LLMError, get_llm_client
from app.generation.pipeline import RAGPipeline
from app.generation.rag import RAGService
from app.query.rewrite import NoOpQueryRewriter
from app.query.understanding import DeterministicQueryUnderstanding
from app.retrieval.embedding import get_embedding_client
from app.schemas.rag import RAGRequest, RAGResponse

router = APIRouter(prefix="/rag", tags=["rag"])
logger = get_logger(__name__)


class RAGOverloadedError(DomainError):
    """并发名额耗尽 → 429。

    与超时区分开：超时是「这次没等到」，满载是「现在别来」，
    调用方应当退避重试，而不是当成上游故障去换 Key / 换模型。
    """

    code = "rag_overloaded"
    http_status = 429


#: 并发闸门：同时进行的推理任务上限，满载立即返回 429 而不是无限排队。
#: 早期版本用 ``with ThreadPoolExecutor`` 每请求建池，``shutdown(wait=True)`` 会把
#: 工作时间完整算进请求耗时，「超时返回 503」只是提前给了个响应。
#: 现在改为「闸门 + 守护工作线程」：并发上限仍由闸门保证，且请求超时后立即返回。
_slots = BoundedSemaphore(max(settings.rag_max_concurrency, 1))

#: 满载时的重试提示。具体释放时间取决于已有任务何时结束，给一个保守的小值，
#: 客户端退避后重试；若仍满载会再次收到 429，而不是盲等一整个请求预算。
_OVERLOAD_RETRY_AFTER_SECONDS = 1


def _default_session() -> Session:
    """后台任务自己开会话，不用请求级 Session。

    超时路径下 FastAPI 依赖已经把请求级会话关闭（真实部署会归还连接池，
    可能被其他请求复用），后台任务再拿它查询属于「现在没炸、换个配置就炸」。
    测试通过 ``dependency_overrides`` 把工厂指向测试库。
    """
    return SessionLocal()


SessionFactory = Callable[[], Session]


def _session_factory_dep() -> SessionFactory:
    """默认会话工厂（可注入，便于测试指向测试库）。"""
    return _default_session


#: 可注入的会话工厂；后台 RAG 任务通过它自己开会话
SessionFactoryDep = Annotated[SessionFactory, Depends(_session_factory_dep)]


def _run_answer(payload: RAGRequest, session_factory: SessionFactory) -> RAGResponse:
    """在工作线程内执行完整 RAG；会话由本线程自己开、自己关。"""
    with session_factory() as session:
        return RAGService.answer(
            session,
            question=payload.question,
            embedding_client=get_embedding_client(),
            llm_factory=get_llm_client,
            top_k=payload.top_k,
            source_types=payload.source_types,
            max_distance=payload.max_distance,
        )


def _run_pipeline_answer(
    payload: RAGRequest, session_factory: SessionFactory
) -> RAGResponse:
    """在工作线程内执行 Phase 18 Pipeline；会话由本线程自己开、自己关。"""
    pipeline = RAGPipeline(
        understanding=DeterministicQueryUnderstanding(),
        rewriter=NoOpQueryRewriter(),
        embedding_client=get_embedding_client(),
        llm=get_llm_client(),
    )
    with session_factory() as session:
        return pipeline.answer(
            session,
            question=payload.question,
            top_k=payload.top_k,
            max_distance=payload.max_distance,
        )


def _execute_with_guards(
    runner: Callable[[RAGRequest, SessionFactory], RAGResponse],
    payload: RAGRequest,
    session_factory: SessionFactory,
) -> RAGResponse:
    """共享的并发闸门 + 超时执行骨架。"""
    try:
        acquired = _slots.acquire(blocking=False)
    except ValueError:  # pragma: no cover - 信号量计数异常，视为满载
        acquired = False
    if not acquired:
        raise RAGOverloadedError(
            "RAG 并发已满，请稍后重试",
            detail={
                "max_concurrency": settings.rag_max_concurrency,
                "retry_after_seconds": _OVERLOAD_RETRY_AFTER_SECONDS,
                "hint": "退避后重试；这不是上游故障，无需换 Key / 换模型",
            },
            headers={"Retry-After": str(_OVERLOAD_RETRY_AFTER_SECONDS)},
        )

    holder: dict[str, object] = {}
    done = Event()

    def worker() -> None:
        try:
            holder["value"] = runner(payload, session_factory)
        except BaseException as exc:  # noqa: BLE001 - 交回主线程按原样抛出
            holder["error"] = exc
        finally:
            # 超时路径也在线程真正结束时才归还名额，否则闸门形同虚设
            done.set()
            try:
                _slots.release()
            except ValueError:
                # 仅在计数超过初始值（逻辑 bug）时发生；不能让它打断线程收尾，
                # 故只记日志。正常路径不可达，也不值得为它造一条用例。
                logger.warning("RAG 并发名额释放异常，已忽略")

    thread = Thread(target=worker, name="rag-answer", daemon=True)
    thread.start()

    if not done.wait(settings.rag_request_timeout):
        logger.warning(
            "RAG 请求超时 timeout=%s，底层调用仍在继续", settings.rag_request_timeout
        )
        raise LLMError(
            "RAG 请求超时",
            detail={"timeout_seconds": settings.rag_request_timeout},
        )

    error = holder.get("error")
    if error is not None:
        raise error  # type: ignore[misc] - 原样抛出，交给统一错误信封
    return holder["value"]  # type: ignore[return-value]


@router.post(
    "/answer",
    response_model=RAGResponse,
    summary="基于知识库回答问题（实验性慢接口）",
    description=(
        "同步执行 Vector Search + LLM。默认 OMLX 模型可能耗时 11–60 秒，"
        "超过 RAG_REQUEST_TIMEOUT 返回 503；并发超过 RAG_MAX_CONCURRENCY 返回 429。"
        "底层调用无法被中断（见 README 已知取舍），暂不建议用于交互式生产场景。"
    ),
)
def answer(payload: RAGRequest, session_factory: SessionFactoryDep) -> RAGResponse:
    """实验性慢接口：真实推理模型可能耗时数十秒。

    已知取舍：超时只是放弃等待，工作线程仍会跑完（最长占用 ``LLM_TIMEOUT``）。
    后台任务用守护线程 + 自己的会话，因此超时返回后请求级连接 / 会话是干净的；
    守护线程让进程退出时不被仍在跑的推理拖住（结果丢弃，但那次请求已经放弃）。
    真正的解法是异步化（Phase 14/20）。
    """
    return _execute_with_guards(_run_answer, payload, session_factory)


@router.post(
    "/answer/pipeline",
    response_model=RAGResponse,
    summary="基于完整 Pipeline 回答问题（实验性慢接口）",
    description=(
        "Phase 18 Pipeline 路径：Query Understanding → Hybrid 检索 → 可选 Rerank → LLM，"
        "响应附带逐阶段 trace。并发 / 超时契约与 /rag/answer 相同；"
        "understanding / rewriter 当前使用确定性默认实现。"
    ),
)
def answer_pipeline(
    payload: RAGRequest, session_factory: SessionFactoryDep
) -> RAGResponse:
    """Pipeline 版回答；trace 在响应的 ``trace`` 字段返回。"""
    return _execute_with_guards(_run_pipeline_answer, payload, session_factory)


__all__ = ["RAGOverloadedError", "router"]
