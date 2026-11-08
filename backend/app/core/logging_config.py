"""
日志配置模块
============

统一配置 Python logging，为整个 EngramNote 应用提供「可定位、可检索、
可关联」的日志体系。改造要点（可观测性增强）：

1. 上下文感知格式：自定义 Formatter 自动从 contextvars 读取
   request_id / user_id / task_id / task_name / biz_id / locale（见 core/context.py），
   每条日志自动携带关联信息——看到任何一条报错日志，就能知道它属于哪个
   请求/任务/用户，直接按 rid=xxx 检索同请求的全部日志。

2. 三路输出：
   - 控制台（StreamHandler）：人类可读格式
   - 全量日志文件 engramnote.log（RotatingFileHandler，轮转保留）
   - 独立错误日志 errors.log：仅 ERROR 及以上，报错定位不再大海捞针
   - JSON 结构化日志 engramnote.json.log：JSON Lines 格式，
     可直接被 logstash / jq / 脚本消费

3. 噪音抑制：uvicorn.access、httpx、sqlalchemy.engine 等第三方日志降噪，
   避免刷屏掩盖业务日志。

## 两条"可机读"的硬约定（2026-11-08 补，均有测试守着）

- **时间戳带毫秒**：`2026-11-07 20:43:00.227`。修复前只有秒，同一秒内的
  多条日志只能靠文件行号排序（实测 47,839 行没有毫秒）。
- **一条记录一行**：消息里的 `\\n` 会被转义成字面量 `\\n`（见 `_single_line`）。
  修复前一条日志可以被换行拆成两条物理行（实测占比 29.36%），
  `grep` / `Select-String` 只拿到前半句。异常堆栈仍然多行——那是它本体，
  JSON 日志里则统一收进单行的 `exc` 字段。

## JSON 的结构化字段

除 `ts/level/logger/message/ctx/exc` 外，业务代码可以通过 `extra=` 直接挂
结构化字段（白名单见 `_EXTRA_PASSTHROUGH`），它们会被**平铺到顶层**，
于是访问日志可以这样聚合，而不必用正则去啃 message：

    jq -c 'select(.event == "http.request" and .status >= 400)' engramnote.json.log
    jq -s 'map(select(.event == "http.request")) | map(.duration_ms) | ...'

用法：
    setup_logging()  # 应用启动时调用一次（FastAPI lifespan / Celery worker 启动）
"""

import json
import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict

from ..config import get_settings
from ..core import context

# 人类可读的文本日志格式（含上下文标签位 %(ctx)s）
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(ctx)s | %(message)s"
# 时间格式（毫秒由 ContextFormatter 追加，见 `_with_milliseconds`）
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 需要抑制的噪音日志器（设为 WARNING 级别）
_NOISY_LOGGERS = [
    "uvicorn.access",
    "uvicorn.error",
    "httpx",
    "httpcore",
    "sqlalchemy.engine",
    "filelock",
    "watchfiles",
]

# JSON 日志里允许从 `record.extra` 平铺到顶层的字段名。
#
# 白名单而不是"平铺所有 extra"：logging 的 record 里本来就有 pathname/lineno/
# process 等几十个内部字段，全平铺会让每条日志多出几百字节，而且 `levelname`
# 之类的键会和我们的 `level` 撞车。
_EXTRA_PASSTHROUGH = (
    "event",
    "status",
    "duration_ms",
    "method",
    "path",
    "error_code",
    "scene",
    "provider",
    "model",
)

# JSON 日志的保留字段：`extra` 不允许覆盖它们
_RESERVED_KEYS = frozenset({"ts", "level", "logger", "message", "ctx", "exc"})


def _single_line(text: str) -> str:
    """
    把消息里的换行转义成字面量 `\\n`，保证"一条记录一行"。

    为什么保留内容而不是直接删掉换行：上游 4xx 的响应体经常是
    `{"error": …}\\n…` 这种多行 JSON，删掉换行会让字段黏在一起、
    反而不可读；转义后既能一眼看出原文有换行，又不会破坏行结构。
    """
    return text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\r")


def _millis(record: logging.LogRecord) -> int:
    """取毫秒部分（`record.msecs` 是浮点，取整后夹到 0..999）"""
    return min(max(int(record.msecs), 0), 999)


def _iso_timestamp(record: logging.LogRecord) -> str:
    """本地时间的 ISO-8601 时间戳，**带时区偏移**（如 `…+08:00`）

    ⚠️ 数据库里的时间（如 `llm_calls.created_at`）是 **UTC**，而日志一直用
    本地时间且不带偏移 —— 跨库对时间时固定差 8 小时（2026-11-07 排查 LLM
    失败时实测踩到）。带上偏移后，`jq` 与本机时钟能直接对上。
    """
    local = time.localtime(record.created)
    offset = time.strftime("%z", local)
    if offset:
        offset = f"{offset[:3]}:{offset[3:]}"
    return f"{time.strftime('%Y-%m-%dT%H:%M:%S', local)}.{_millis(record):03d}{offset}"


class ContextFormatter(logging.Formatter):
    """
    上下文感知 Formatter

    在每条日志中注入 request_id/user_id/task_id/locale 等关联信息：
    - 文本格式：在 %(ctx)s 位置输出 "[rid=..] [uid=..] [tid=..] [task=..] [biz=..] [loc=..]"
    - 时间戳精确到毫秒（`_with_milliseconds`）
    - 消息里的换行被转义（`_single_line`），保证一条记录一行
    - 异常日志自动附带完整 traceback（标准 exc_info 行为）
    """

    def format(self, record: logging.LogRecord) -> str:
        # 注入上下文标签（时间戳的毫秒在 `formatTime` 里补，见下）
        record.ctx = context.context_tag()
        return super().format(record)

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        """时间戳精确到毫秒

        ⚠️ 必须覆写 `formatTime`，**不能**在 `format()` 里给 `record.asctime`
        赋值：`Formatter.format()` 自己会再调一次 `formatTime` 把它覆盖掉
        （第一次实现就是这么写的，测试直接红）。也不要用 `default_msec_format`
        —— 它只在 `datefmt` 为 None 时生效。
        """
        base = super().formatTime(record, datefmt=datefmt)
        return f"{base}.{_millis(record):03d}"

    def formatMessage(self, record: logging.LogRecord) -> str:
        """先按标准流程插值（`%s` 参数），再把换行转义掉

        覆写 `formatMessage` 而不是改 `record.msg`：同一条 record 会被多个
        handler 依次格式化，改 record 会把"已被转义过的字符串"传给后面的
        JSON handler（它自己会用 `json.dumps` 转义）。
        """
        return _single_line(super().formatMessage(record))


class JsonLinesFormatter(logging.Formatter):
    """
    JSON Lines Formatter

    输出单行 JSON，字段：
    - `ts` / `level` / `logger` / `message` / `ctx`（字典）/ `exc`（异常摘要）
    - `record.extra` 里的白名单字段（`_EXTRA_PASSTHROUGH`）平铺到顶层，
      例如访问日志的 `event=http.request` / `status` / `duration_ms` / `path`

    保证 JSON 可解析：异常信息统一转为字符串，message 里的换行由
    `json.dumps` 转义。
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": _iso_timestamp(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "ctx": context.context_dict(),
        }
        for key in _EXTRA_PASSTHROUGH:
            if key in _RESERVED_KEYS:
                continue
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _make_handler(
    stream_or_path,
    formatter: logging.Formatter,
    level: int,
    backup_count: int | None = None,
) -> logging.Handler:
    """建 handler；文件 handler 的轮转份数按"这份日志的用途"取值

    `backup_count=None` → 用 `settings.log_backup_count`（兼容老配置/控制台）。
    """
    handler: logging.Handler
    if isinstance(stream_or_path, Path):
        settings = get_settings()
        handler = RotatingFileHandler(
            str(stream_or_path),
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count if backup_count is None else backup_count,
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
    # 2) 全量日志文件（轮转）—— 人读主日志，留一周左右即够
    root_logger.addHandler(_make_handler(log_file, text_formatter, log_level, settings.log_text_backup_count))
    # 3) 独立错误日志（ERROR 及以上，快速定位）—— 量小，留 10 份
    root_logger.addHandler(_make_handler(error_file, text_formatter, logging.ERROR, settings.log_error_backup_count))
    # 4) JSON 结构化日志（JSON Lines，机器可读）—— 排障主通道，留最久
    root_logger.addHandler(_make_handler(json_file, json_formatter, log_level, settings.log_json_backup_count))

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

    logging.getLogger("engramnote").info("日志系统初始化完成: level=%s dir=%s", settings.log_level, log_dir)


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
