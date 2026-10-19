"""LLM 配置与 OpenAI-compatible 客户端测试。"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.generation.llm import LLMError, OpenAICompatibleLLMClient


class TestLLMConfig:
    def test_default_config_points_to_local_omlx(self) -> None:
        config = Settings(_env_file=None)

        assert config.llm_provider == "openai_compatible"
        assert config.llm_base_url == "http://127.0.0.1:28080/v1"
        assert config.llm_api_key == "omlx"
        assert config.llm_model == "Ling-3.0-tiny-oQ6e"

    def test_openai_compatible_requires_api_key(self) -> None:
        config = Settings(
            _env_file=None,
            llm_provider="openai_compatible",
            llm_api_key="",
        )

        with pytest.raises(LLMError, match="LLM_API_KEY"):
            OpenAICompatibleLLMClient(config)
