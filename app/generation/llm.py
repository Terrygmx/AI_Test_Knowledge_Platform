"""LLM 抽象与 OpenAI-compatible 实现。

Phase 3 只依赖最小的 ``generate(prompt) -> str`` 接口，不引入 LangChain /
LlamaIndex。这样 RAG Pipeline 的检索、上下文组装和引用校验都保持显式可测。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from openai import OpenAI

from app.core.config import Settings, settings
from app.core.exceptions import DomainError


class LLMError(DomainError):
    """LLM 配置缺失、上游失败或输出不合法。"""

    code = "llm_error"
    http_status = 503


@runtime_checkable
class LLMClient(Protocol):
    """生成模型的最小协议。"""

    def generate(self, prompt: str) -> str: ...


class OpenAICompatibleLLMClient:
    """OpenAI-compatible Chat Completions 客户端。

    同时支持本地 Ollama（``/v1``）与云 API；两者都走同一套接口。
    """

    def __init__(self, config: Settings | None = None) -> None:
        self._settings = config or settings
        if self._settings.llm_provider == "openai_compatible" and not self._settings.llm_api_key:
            raise LLMError("LLM_API_KEY 未配置")
        self._client = OpenAI(
            api_key=self._settings.llm_api_key or "ollama",
            base_url=self._settings.llm_base_url,
            timeout=self._settings.llm_timeout,
            max_retries=1,
        )

    def generate(self, prompt: str) -> str:
        if not prompt.strip():
            raise LLMError("LLM 输入不能为空")
        try:
            response = self._client.chat.completions.create(
                model=self._settings.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self._settings.llm_temperature,
                max_tokens=self._settings.llm_max_tokens,
            )
        except Exception as exc:  # noqa: BLE001 - 统一转换成领域错误
            raise LLMError(
                "LLM 上游调用失败",
                detail={"model": self._settings.llm_model},
            ) from exc

        content = response.choices[0].message.content if response.choices else None
        if not isinstance(content, str) or not content.strip():
            raise LLMError("LLM 返回了空回答")
        return content.strip()


def get_llm_client(config: Settings | None = None) -> LLMClient:
    """应用默认 LLM 客户端。"""
    return OpenAICompatibleLLMClient(config)


__all__ = ["LLMClient", "LLMError", "OpenAICompatibleLLMClient", "get_llm_client"]
