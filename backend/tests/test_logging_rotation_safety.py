# -*- coding: utf-8 -*-
"""多进程共用日志文件时的轮转兜底（2026-11-08 现场发现）

## 现场（API + Celery worker 同时运行，第一次真跑）

```
--- Logging error ---
Traceback (most recent call last):
  File "…/logging/handlers.py", line 74, in emit
    self.doRollover()
  …
PermissionError: [WinError 32] 另一个程序正在使用此文件，进程无法访问。:
  'D:\\engramnote\\backend\\data\\logs\\engramnote.log'
  -> 'D:\\engramnote\\backend\\data\\logs\\engramnote.log.1'
```

Windows 不允许重命名"被另一个进程打开着"的文件，而 API 与 worker 都通过
`setup_logging()` 挂了同一个 `engramnote.log` 的 handler。后果不是"轮转失败"
这么轻：`RotatingFileHandler.emit()` 先 `doRollover()` 失败 → 异常冒到
`Handler.handleError` → **这条记录被丢弃**；而文件依然超过 `maxBytes`，
于是**之后每一条日志都失败**。实测主日志卡在 10.4MB 不再增长（mtime 冻住），
也就是那一整段时间"日志全瞎"。

## 守的判据

1. 轮转失败时**记录照样落盘**（宁可文件大一点，也不要整段时间没有日志）；
2. 失败只告警一次（不刷屏），并在一段时间内不再重试 rename（避免每条日志
   都去撞一次必然失败的 rename）。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.core.logging_config import _SafeRotatingFileHandler


def _make_handler(path: Path) -> _SafeRotatingFileHandler:
    handler = _SafeRotatingFileHandler(str(path), maxBytes=1, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    return handler


@pytest.fixture
def logger_with(tmp_path):
    created: list[_SafeRotatingFileHandler] = []

    def _factory(name: str) -> tuple[logging.Logger, _SafeRotatingFileHandler]:
        handler = _make_handler(tmp_path / f"{name}.log")
        created.append(handler)
        logger = logging.getLogger(f"test.safe_rotate.{name}")
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False
        return logger, handler

    yield _factory
    for handler in created:
        handler.close()


def test_record_is_not_lost_when_rollover_raises(logger_with, monkeypatch, caplog):
    """★ 轮转被 Windows 拒绝时，这条日志**必须还在**（改造前会被丢弃）"""
    logger, _handler = logger_with("blocked")

    def _boom(self):  # noqa: ANN001 - 模拟 Windows 的 PermissionError
        raise PermissionError(32, "另一个程序正在使用此文件，进程无法访问。")

    # ⚠️ 必须打在**父类**上：打在子类上等于把我的兜底一起换掉，测的就不是兜底了
    monkeypatch.setattr("logging.handlers.RotatingFileHandler.doRollover", _boom)

    with caplog.at_level(logging.WARNING):
        logger.info("这条必须落盘")

    written = Path(logger.handlers[0].baseFilename).read_text(encoding="utf-8")
    assert "这条必须落盘" in written, f"轮转失败把日志吞了: {written!r}"
    assert any("轮转失败" in r.getMessage() for r in caplog.records), "轮转失败没有告警"


def test_rollover_warns_only_once(logger_with, monkeypatch, caplog):
    """失败只告警一次：每条日志都喊一遍等于用噪音淹没问题"""
    logger, _handler = logger_with("once")

    def _boom(self):  # noqa: ANN001
        raise PermissionError(32, "busy")

    monkeypatch.setattr("logging.handlers.RotatingFileHandler.doRollover", _boom)

    with caplog.at_level(logging.WARNING):
        for i in range(5):
            logger.info("第 %d 条", i)

    warnings = [r for r in caplog.records if "轮转失败" in r.getMessage()]
    assert len(warnings) == 1, f"告警了 {len(warnings)} 次"


def test_rollover_still_works_normally(logger_with):
    """反向判据：没有进程争用时，轮转必须照常发生（不能把功能"修没了"）

    注意**不要**断言"文件不超过 maxBytes"：`shouldRollover` 在**写入前**判断，
    所以单条记录可以让文件越过上限一次 —— 这是 `RotatingFileHandler` 的固有行为。
    """
    logger, handler = logger_with("normal")
    for i in range(50):
        logger.info("行 %d 填充内容", i)

    rotated = Path(f"{handler.baseFilename}.1")
    current = Path(handler.baseFilename)
    assert rotated.exists(), "正常轮转没发生"
    assert current.stat().st_size < rotated.stat().st_size or current.stat().st_size <= 64
