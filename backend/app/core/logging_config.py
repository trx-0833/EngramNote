"""
日志配置模块
============

统一配置 Python logging，为整个 EngramNote 应用提供「可定位、可检索、
可关联」的日志体系。改造要点（可观测性增强）：

1. 上下文感知格式：自定义 Formatter 自动从 contextvars 读取
   request_id / user_id / task_id / task_name / biz_id（见 core/context.py），
   每条日志自动携带关联信息——看到任何一条报错日志，就能知道它属于哪个
   请求/任务/用户，直接按 rid=xxx 检索同请求的全部日志。

2. 三路输出：
   - 控制台（StreamHandler）：人类可读格式
   - 全量日志文件 engramnote.log（RotatingFileHandler，轮转保留）
   - 独立错误日志 errors.log：仅 ERROR 及以上，报错定位不再大海捞针
   - JSON 结构化日志 engramnote.json.log：JSON Lines 格式，
     可直接被 logstash / jq / 脚本消费

3. 噪音抑制：uvicorn.access、httpx、chromadb 等第三方日志降噪，
   避免刷屏掩盖业务日志。

用法：
    setup_logging()  # 应用启动时调用一次（FastAPI lifespan / Celery worker 启动）
"""

import json
import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from ..config import get_settings
from ..core import context

# 人类可读的文本日志格式（含上下文标签位 %(ctx)s）
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(ctx)s | %(message)s"
# 时间格式（含毫秒，便于精确排序同请求日志）
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 需要抑制的噪音日志器（设为 WARNING 级别）
_NOISY_LOGGERS = [
    "uvicorn.access",
    "uvicorn.error",
    "httpx",
    "httpcore",
    "sqlalchemy.engine",
    "filelock",
    "chromadb",
    "watchfiles",
]


class ContextFormatter(logging.Formatter):
    """
    上下文感知 Formatter

    在每条日志中注入 request_id/user_id/task_id 等关联信息：
    - 文本格式：在 %(ctx)s 位置输出 "[rid=..] [uid=..] [tid=..] [task=..] [biz=..]"
    - 异常日志自动附带完整 traceback（标准 exc_info 行为）
    """

    def format(self, record: logging.LogRecord) -> str:
        # 记录当前时间（毫秒精度）
        record.asctime = self.formatTime(record, self.datefmt)
        # 注入上下文标签
        record.ctx = context.context_tag()
        return super().format(record)


class JsonLinesFormatter(logging.Formatter):
    """
    JSON Lines Formatter

    输出单行 JSON，字段：ts/level/logger/message/ctx(字典)/exc(异常摘要)。
    保证 JSON 可解析：异常信息统一转为字符串，message 去换行。
    """

    def format(self, record: logging.LogRecord) -> str:
        ctx = context.context_dict()
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created))
            + f".{int(record.msecs):03d}",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "ctx": ctx,
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _make_handler(stream_or_path, formatter: logging.Formatter, level: int) -> logging.Handler:
    handler: logging.Handler
    if isinstance(stream_or_path, Path):
        handler = RotatingFileHandler(
            str(stream_or_path),
            maxBytes=get_settings().log_max_bytes,
            backupCount=get_settings().log_backup_count,
            encoding="utf-8",
        )
    else:
        handler = logging.StreamHandler(stream_or_path)
    handler.setLevel(level)
    handler.setFormatter(formatter)
    return handler


def setup_logging() -> None:
    """
    初始化日志配置（应用启动时调用一次）

    配置 root logger：控制台 + 全量文件 + 错误文件 + JSON 文件四路输出。
    所有 engramnote 命名空间日志自动携带请求/任务上下文。
    """
    settings = get_settings()
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    log_dir: Path = settings.get_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "engramnote.log"
    error_file = log_dir / "errors.log"
    json_file = log_dir / "engramnote.json.log"

    # 配置根 logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    # 清除已有 handler，避免重复添加（如 uvicorn 自动配置的）
    root_logger.handlers.clear()

    # 文本格式化器（上下文感知）
    text_formatter = ContextFormatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    # JSON 格式化器
    json_formatter = JsonLinesFormatter()

    # 1) 控制台
    root_logger.addHandler(_make_handler(None, text_formatter, log_level))
    # 2) 全量日志文件（轮转）
    root_logger.addHandler(_make_handler(log_file, text_formatter, log_level))
    # 3) 独立错误日志（ERROR 及以上，快速定位）
    root_logger.addHandler(_make_handler(error_file, text_formatter, logging.ERROR))
    # 4) JSON 结构化日志（JSON Lines，机器可读）
    root_logger.addHandler(_make_handler(json_file, json_formatter, log_level))

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

    # 访问日志 logger（RequestContextMiddleware 使用）
    access_logger = logging.getLogger("engramnote.access")
    access_logger.setLevel(log_level)
    access_logger.propagate = True

    logging.getLogger("engramnote").info(
        "日志系统初始化完成: level=%s dir=%s", settings.log_level, log_dir
    )


def log_exception(logger: logging.Logger, exc: BaseException, message: str = "未处理异常") -> None:
    """
    统一异常日志辅助函数：输出完整堆栈 + 当前上下文

    Args:
        logger: 用于记录日志的 logger
        exc: 异常对象
        message: 日志前缀说明
    """
    logger.error(
        "%s | type=%s | detail=%s",
        message,
        type(exc).__name__,
        exc,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
