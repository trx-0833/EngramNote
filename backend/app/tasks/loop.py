"""Celery 任务的**任务级事件循环**（阶段 4.5）

## 问题：一个任务里 6 次 `asyncio.run` = 6 个事件循环

改造前每个任务体是这样的（`understand_document_task` 实测）：

    asyncio.run(_update_note_status(...))   # loop 1
    asyncio.run(_report_progress(...))      # loop 2
    asyncio.run(_understand_document(...))  # loop 3
    asyncio.run(_report_progress(...))      # loop 4
    asyncio.run(mark_succeeded(...))        # loop 5

每一次都新建一个 loop、跑完再关掉。后果不是"慢一点"，而是三类真实故障：

1. **连接池全部作废**：httpx 客户端按 loop 判定失效并重建（`client.py`），
   于是"进程级共享客户端"在 Celery 里实际是"每次调用新建一个" ——
   连接复用与 TLS 握手优化一次都没生效；
2. **数据库连接跨 loop 复用**：worker 侧的 `aiosqlite` 连接是模块级单例
   （`tasks/common.py`），却在 5 个不同的 loop 里被使用。目前"能跑"
   依赖 aiosqlite 内部按当前 loop 新建 Future 的实现细节，属于**偶然正确**；
3. **状态无处安放**：任何"一次任务内共享"的东西（进度缓存、会话、临时句柄）
   都无法依附在一个稳定对象上。

## 做法：一个任务一个 loop

    with task_loop("understand_document"):
        run_async(_update_note_status(...))   # ┐
        run_async(_report_progress(...))      # │ 全部在同一个 loop 上
        run_async(_understand_document(...))  # │
        run_async(mark_succeeded(...))        # ┘

`run_async` 在 `task_loop` 之外调用时**退回 `asyncio.run`** —— 因此
`tasks/common.py` 里的公共助手不必关心自己是在任务里还是在脚本里被调用。

## 为什么不是"把任务体改成 async 函数"

计划里的原话是"`asyncio.run()` 一次包裹整个任务体"。字面上那要求把
6 个任务模块的函数体整体改写为 `async def`，并把 Celery 的 `self.retry()`、
日志、`finally` 清理全部搬进协程 —— 那些是**同步**的 Celery 语义，
搬进去只会让重试路径更难读。

本模块用"同一个 loop 跑完所有协程"达到同一目的：
**一个任务内的所有异步代码共享一个 loop**，而任务体保持同步。
差别只在"task 本身是不是协程"，而这一点对上述三类故障毫无影响。

## 退出时清理

loop 关闭前会：取消仍在挂起的任务并等它们收尾、关闭异步生成器、再 `close()`。
不这么做的话，一个被 `self.retry()` 抛断的任务会留下挂起的协程，
在日志里表现为 `Task was destroyed but it is pending!` —— 那是噪音，
但会掩盖真正的错误信息。
"""

import asyncio
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Awaitable, Iterator, Optional, TypeVar

logger = logging.getLogger("engramnote.tasks")

T = TypeVar("T")

#: 当前任务的事件循环（`task_loop` 之外为 None）
_current_task_loop: ContextVar[Optional[asyncio.AbstractEventLoop]] = ContextVar(
    "engramnote_task_loop", default=None,
)


def current_task_loop() -> Optional[asyncio.AbstractEventLoop]:
    """取当前任务的事件循环（不在任务里时返回 None；供测试与诊断使用）"""
    return _current_task_loop.get()


@contextmanager
def task_loop(name: str = "") -> Iterator[asyncio.AbstractEventLoop]:
    """开启一个任务级事件循环，`with` 块内的 `run_async` 全部复用它

    Args:
        name: 任务名，仅用于日志（`asyncio.run` 的调试名同理）
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    token = _current_task_loop.set(loop)
    try:
        yield loop
    finally:
        _current_task_loop.reset(token)
        _shutdown(loop, name)
        # 不要把已关闭的 loop 留在 thread-local 里：下一个任务会自己建，
        # 而 `asyncio.get_event_loop()` 拿到一个已关闭的 loop 会报
        # "Event loop is closed"（旧实现踩过的正是这个）。
        asyncio.set_event_loop(None)


def run_async(coro: Awaitable[T], *, name: str = "") -> T:
    """在**当前任务的**事件循环上运行协程；不在任务里时等同 `asyncio.run`

    ⚠️ 返回值与异常语义与 `asyncio.run` 一致（异常原样抛出）。
    """
    loop = _current_task_loop.get()
    if loop is None:
        return asyncio.run(coro)  # type: ignore[arg-type]

    if loop.is_closed():  # pragma: no cover - 只在误用（提前 close）时触发
        raise RuntimeError(
            "任务级事件循环已被关闭：run_async 不能在 task_loop 的 with 块之外使用"
        )
    return loop.run_until_complete(coro)  # type: ignore[arg-type]


def _shutdown(loop: asyncio.AbstractEventLoop, name: str) -> None:
    """关闭 loop 前收拾干净（挂起任务 → 异步生成器 → close）"""
    try:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        if pending:
            logger.debug("任务 %s 结束时有 %d 个挂起协程，正在取消", name or "?", len(pending))
            for task in pending:
                task.cancel()
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.run_until_complete(loop.shutdown_asyncgens())
    except Exception as exc:  # noqa: BLE001 - 清理失败不应把成功的任务变成失败
        logger.debug("任务级事件循环清理异常（忽略）: %s", exc)
    finally:
        loop.close()


__all__ = ["current_task_loop", "run_async", "task_loop"]
