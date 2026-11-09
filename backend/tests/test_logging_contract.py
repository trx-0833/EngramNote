"""日志契约测试（2026-11-08 新增）
=====================================

**这个文件为什么存在**：`docs/operations.md` §3 把「拿到 request_id → 去日志里
按 rid 检索整条请求」写成了本系统的主排障路径，而这条路径此前**没有任何测试
守着**。2026-11-07 的实测结果：

- `engramnote.access` 的 **13,225** 条「请求完成」里，带 rid 的是 **0** 条
  —— 中间件把 `context.reset_context()` 写在 `finally` 里，访问日志在
  try/finally **之后**才写，上下文已经清空；
- 文本日志时间戳**没有毫秒**（47,839 行），而格式化器的注释写着"含毫秒"；
- 29.36% 的行是"续行"：一条日志里的换行把记录拆成两行，行式工具只拿到半句；
- JSON 日志只有 `ts/level/logger/message/ctx/exc`，`status` / 耗时 / 路径全在
  `message` 字符串里 —— 文档承诺的"从 json.log 的访问日志聚合 P95"做不到。

以下 5 条契约，每条对应上面一条实测缺陷。断言都写在**格式化器与中间件的
边界**上（不依赖真实的日志目录），跑起来是毫秒级。
"""

from __future__ import annotations

import io
import json
import logging
import re
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import context
from app.core.logging_config import (
    LOG_DATE_FORMAT,
    LOG_FORMAT,
    ContextFormatter,
    JsonLinesFormatter,
)
from app.middleware.request_context import RequestContextMiddleware
from app.services.auth_service import create_access_token

# 时间戳必须带毫秒，例如 `2026-11-07 20:43:00.227`
_TS_WITH_MS = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} \|")
# JSON 的 ts 必须带时区偏移，例如 `2026-11-07T20:43:00.227+08:00`
_TS_OFFSET = re.compile(r"[+-]\d{2}:\d{2}$")


@pytest.fixture
def access_stream() -> Iterator[io.StringIO]:
    """把 `engramnote.access` 的日志接到内存里（不碰真实日志目录）"""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(ContextFormatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
    access_logger = logging.getLogger("engramnote.access")
    old_level, old_propagate, old_handlers = (
        access_logger.level,
        access_logger.propagate,
        list(access_logger.handlers),
    )
    access_logger.handlers = [handler]
    access_logger.setLevel(logging.INFO)
    access_logger.propagate = False
    try:
        yield stream
    finally:
        access_logger.handlers = old_handlers
        access_logger.setLevel(old_level)
        access_logger.propagate = old_propagate


def _record(message: str = "测试消息", **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="engramnote.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


# ── 契约 1：访问日志必须带 rid / uid ──────────────────────────────────────


def test_access_log_line_carries_request_and_user_context(
    access_stream: io.StringIO,
) -> None:
    """「请求完成」是排障入口，必须能按 rid 与同请求的其他日志对上

    回归证据：修复前 13,225 条访问日志里 0 条带 rid —— `reset_context()`
    在 `finally` 里先执行，访问日志在它之后才写。
    """
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    token = create_access_token("user-ctx-1")
    with TestClient(app) as client:
        resp = client.get("/ping", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    line = access_stream.getvalue().strip()
    assert "请求完成" in line
    assert "rid=" in line, f"访问日志丢了 request_id：{line!r}"
    assert "uid=user-ctx-1" in line, f"访问日志丢了 user_id：{line!r}"
    # 响应头也要能反查（前端把 rid 展示给用户）
    assert resp.headers["X-Request-ID"]


# ── 契约 2：文本日志时间戳带毫秒 ──────────────────────────────────────────


def test_text_formatter_timestamp_has_milliseconds() -> None:
    """同秒多条时必须能排序（修复前只有秒，靠文件行号才排得出先后）"""
    out = ContextFormatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT).format(_record())
    assert _TS_WITH_MS.match(out), f"时间戳没有毫秒：{out!r}"


# ── 契约 3：一条记录一行 ─────────────────────────────────────────────────


def test_message_with_newlines_stays_on_one_line() -> None:
    """消息里的换行必须被转义

    真实来源：上游 4xx 的响应体（httpx 异常消息里带 `\\n`）曾把
    `body={"error": …}` 甩到下一条物理行，`Select-String`/`grep` 只拿到前半句。
    """
    out = ContextFormatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT).format(
        _record('上游失败 status=400 body={"error": "Model is unavailable."}\n第二行')
    )
    assert out.count("\n") == 0, f"一条日志被拆成了多行：{out!r}"
    assert "第二行" in out  # 内容不能丢，只是换行被转义
    assert "body=" in out


def test_exception_traceback_is_kept_but_record_still_parseable() -> None:
    """异常堆栈允许占多行（那是它的本体），但**首行仍是半结构化的头**"""
    try:
        raise ValueError("演示异常")
    except ValueError:
        import sys

        record = _record("处理失败", exc_info=sys.exc_info())

    text = ContextFormatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT).format(record)
    first_line, _, rest = text.partition("\n")
    assert _TS_WITH_MS.match(first_line)
    assert "处理失败" in first_line
    assert "ValueError: 演示异常" in rest


# ── 契约 4：JSON 日志一行一条 + 结构化字段 ───────────────────────────────


def test_json_line_has_structured_access_fields() -> None:
    """status / duration_ms / method / path 必须是**字段**，不是 message 里的字

    否则文档里那条"从 json.log 聚合 P95"只能用正则重新解析字符串。
    """
    context.set_request_context(
        request_id="rid-json-1",
        user_id="u-json",
        method="GET",
        path="/api/notes",
        locale="zh-CN",
    )
    try:
        record = _record(
            "请求完成 | GET /api/notes | status=400 | 耗时 12ms",
            event="http.request",
            status=400,
            duration_ms=12.5,
            method="GET",
            path="/api/notes",
            error_code="NOTE_NOT_FOUND",
        )
        raw = JsonLinesFormatter().format(record)
    finally:
        context.reset_context()

    assert "\n" not in raw, "JSON 日志被拆成了多行"
    payload = json.loads(raw)
    assert payload["event"] == "http.request"
    assert payload["status"] == 400
    assert payload["duration_ms"] == 12.5
    assert payload["method"] == "GET"
    assert payload["path"] == "/api/notes"
    assert payload["error_code"] == "NOTE_NOT_FOUND"
    assert payload["ctx"]["request_id"] == "rid-json-1"
    assert _TS_OFFSET.search(payload["ts"]), f"ts 没有时区偏移：{payload['ts']!r}"


def test_json_line_keeps_exception_as_single_string() -> None:
    """异常在 JSON 里必须是一个字符串字段，而不是把记录撑成多行"""
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        import sys

        record = _record("炸了", exc_info=sys.exc_info())

    raw = JsonLinesFormatter().format(record)
    assert "\n" not in raw
    payload = json.loads(raw)
    assert "RuntimeError: boom" in payload["exc"]


# ── 契约 5：上下文里要有 locale ──────────────────────────────────────────


def test_context_dict_includes_locale() -> None:
    """i18n 排障要看"这个请求索赔的是哪种语言"（`set_request_context` 已保存它）

    文本标签沿用 `rid/uid/tid` 的短名风格，写作 `loc=`；JSON 里是 `ctx.locale`。
    """
    context.set_request_context(
        request_id="rid-locale",
        user_id="u",
        method="POST",
        path="/api/x",
        locale="en",
    )
    try:
        assert context.context_dict().get("locale") == "en"
        assert "loc=en" in context.context_tag()
    finally:
        context.reset_context()


def test_context_dict_omits_locale_when_absent() -> None:
    """没设语言（例如 Celery 任务）时不要凭空造一个 `locale` 字段"""
    context.reset_context()
    assert "locale" not in context.context_dict()
