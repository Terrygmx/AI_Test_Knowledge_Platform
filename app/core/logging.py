"""统一日志配置。

RAG 的每个阶段（understanding / retrieval / fusion / rerank / generation）
都需要可观测，Badcase 分析依赖这些日志，因此从第一天就统一格式。
"""

import logging
import sys

from app.core.config import settings

_CONFIGURED = False
_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-28s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: str | None = None) -> None:
    """初始化根 logger，重复调用无副作用。"""
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.basicConfig(
        level=(level or settings.log_level).upper(),
        format=_FORMAT,
        datefmt=_DATEFMT,
        stream=sys.stdout,
        force=True,
    )
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """获取带统一配置的 logger。"""
    setup_logging()
    return logging.getLogger(name)
