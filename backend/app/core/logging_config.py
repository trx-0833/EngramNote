"""
日志配置模块

本模块负责统一配置 Python logging，支持双输出（控制台 + 文件），
并为整个 EngramNote 应用提供一致的日志格式和管理策略。

主要职责：
- 配置控制台输出（StreamHandler），使用配置的 LOG_LEVEL
- 配置文件输出（RotatingFileHandler），自动轮转避免日志文件过大
- 设置统一的日志格式
- 配置 engramnote 命名空间日志
- 抑制第三方库的噪音日志（uvicorn.access、httpx 等）
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..config import get_settings

# 统一日志格式
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

# 需要抑制的噪音日志器（设为 WARNING 级别）
_NOISY_LOGGERS = [
    "uvicorn.access",
    "uvicorn.error",
    "httpx",
    "httpcore",
    "sqlalchemy.engine",
    "filelock",
    "chromadb",
]


def setup_logging() -> None:
    """
    初始化日志配置

    在应用启动时调用一次，配置 root logger 和 engramnote 命名空间日志。
    支持双输出：控制台（StreamHandler）+ 文件（RotatingFileHandler）。

    调用时机：在 FastAPI lifespan 中、数据库初始化之前调用。
    """
    settings = get_settings()
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    log_dir = settings.get_log_dir()

    # 确保日志目录存在
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "engramnote.log"

    # 配置根 logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # 清除已有 handler，避免重复添加（如 uvicorn 自动配置的）
    root_logger.handlers.clear()

    # 创建格式化器
    formatter = logging.Formatter(LOG_FORMAT)

    # 控制台 handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 文件 handler（RotatingFileHandler，自动轮转）
    file_handler = RotatingFileHandler(
        str(log_file),
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # 配置 engramnote 命名空间日志
    engramnote_logger = logging.getLogger("engramnote")
    engramnote_logger.setLevel(log_level)

    # 抑制噪音日志器
    for noisy_name in _NOISY_LOGGERS:
        noisy_logger = logging.getLogger(noisy_name)
        noisy_logger.setLevel(logging.WARNING)

    # 配置 LLM 调用专用 logger（确保响应日志始终输出）
    llm_logger = logging.getLogger("engramnote.llm")
    llm_logger.setLevel(logging.INFO)  # 响应日志始终在 INFO 级别输出
    llm_logger.propagate = True  # 传播到 root logger，使用 root 的 handlers
