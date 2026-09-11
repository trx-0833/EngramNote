"""阶段 4.5：任务级事件循环测试

## 这份测试要证明什么

4.5 的目标是"一个任务一个事件循环"。这件事**无法靠观察日志确认** ——
6 次 `asyncio.run` 和 1 次 `task_loop` 在功能上看起来一样（任务都跑完了），
差别只在"连接池有没有被反复作废""跨 loop 复用有没有踩到偶然正确"。
因此这里直接对**循环身份**下断言：

| 要证明的事 | 为什么 | 对应测试 |
|---|---|---|
| `with` 块内所有协程跑在同一个 loop 上 | 这正是 4.5 的全部内容 | `test_all_calls_share_one_loop` |
| 任务结束后 loop 被关闭、thread-local 被清干净 | 留着已关闭的 loop 会让下一个任务报 "Event loop is closed" | `test_loop_is_closed_and_unset_after_exit` |
| 没有任务循环时退回 `asyncio.run` | `common.py` 的助手要被脚本/测试复用 | `test_falls_back_to_asyncio_run_outside_task` |
| 挂起的协程在关闭前被取消 | 否则日志里全是 "Task was destroyed but it is pending" | `test_pending_task_is_cancelled_on_exit` |
| 异常原样抛出、loop 照样关闭 | 失败路径不该泄漏 loop | `test_exception_propagates_and_loop_still_closes` |

## 为什么用 `current_task_loop()` 而不是打印日志

`get_running_loop()` 返回的对象身份是"同一个 loop"这件事的**唯一**判据。
用 id 不行（loop 被 GC 后 id 会复用，附录 W 踩过）；用日志文本更不行
（那是在断言字符串，不是在断言行为）。
"""

import asyncio

import pytest

from app.tasks.loop import current_task_loop, run_async, task_loop


class TestTaskLoop:
    """全部是**同步**用例：`task_loop` 本来就是"给同步 Celery 任务用"的东西，
    用 pytest-asyncio 提供的 loop 来测它就本末倒置了。"""
    def test_all_calls_share_one_loop(self):
        """★ 核心：同一个任务里的多次调用共享一个 loop"""
        seen = []

        async def record() -> int:
            seen.append(asyncio.get_running_loop())
            return len(seen)

        with task_loop("unit_test"):
            results = [run_async(record()) for _ in range(5)]
            inside = current_task_loop()

        assert results == [1, 2, 3, 4, 5]
        assert len(set(map(id, seen))) == 1, "多次调用跑在了不同的 loop 上"
        assert seen[0] is inside, "任务循环与协程实际运行的 loop 不是同一个"

    def test_loop_differs_between_tasks(self):
        """不同任务必须是不同 loop —— 否则"每任务一个 loop"只是说法"""
        async def this_loop():
            return asyncio.get_running_loop()

        with task_loop("task_a"):
            first = run_async(this_loop())
        with task_loop("task_b"):
            second = run_async(this_loop())

        assert first is not second
        assert first.is_closed() and second.is_closed()

    def test_loop_is_closed_and_unset_after_exit(self):
        """退出后 loop 已关闭，且 thread-local 不残留已关闭的 loop"""
        with task_loop("unit_test") as loop:
            run_async(asyncio.sleep(0))
            assert not loop.is_closed()

        assert loop.is_closed()
        assert current_task_loop() is None
        # 直接取 thread-local：必须为 None，否则 asyncio.get_event_loop()
        # 会把一个已关闭的 loop 交给下一个任务（旧实现的 "Event loop is closed"）
        with pytest.raises(RuntimeError):
            asyncio.get_event_loop_policy().get_event_loop()

    def test_falls_back_to_asyncio_run_outside_task(self):
        """不在 task_loop 里时等同 asyncio.run：自己建 loop、自己关"""
        loop_seen = {}

        async def record():
            loop = asyncio.get_running_loop()
            loop_seen["loop"] = loop
            return "ok"

        assert run_async(record()) == "ok"
        assert loop_seen["loop"].is_closed(), "回退路径没有关闭自己创建的 loop"

    def test_value_and_exception_semantics_match_asyncio_run(self):
        """返回值原样传出；异常原样抛出（含自定义异常类型）"""
        class Boom(Exception):
            pass

        async def ok():
            return {"a": 1}

        async def bad():
            raise Boom("炸了")

        with task_loop("unit_test"):
            assert run_async(ok()) == {"a": 1}
            with pytest.raises(Boom):
                run_async(bad())
            # 一次异常之后循环仍然可用（否则重试路径就没法在同 loop 里补救）
            assert run_async(ok()) == {"a": 1}

    def test_exception_propagates_and_loop_still_closes(self):
        with pytest.raises(ValueError, match="boom"):
            with task_loop("unit_test") as loop:
                run_async(asyncio.sleep(0))
                raise ValueError("boom")
        assert loop.is_closed()
        assert current_task_loop() is None

    def test_pending_task_is_cancelled_on_exit(self):
        """退出时挂起的协程被取消并收尾（不留噪音，也不留半截写入）"""
        state = {"cancelled": False, "cleaned": False}

        async def long_running():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise
            finally:
                state["cleaned"] = True

        with task_loop("unit_test") as loop:
            loop.create_task(long_running())
            loop.run_until_complete(asyncio.sleep(0.01))   # 让任务真正跑起来

        assert state == {"cancelled": True, "cleaned": True}


# ---------------------------------------------------------------------------
# 端到端：真实的 Celery 公共助手（common.py）在同一个 loop 上
# ---------------------------------------------------------------------------

class TestCommonHelpersShareTaskLoop:
    """`tasks/common.py` 的四个助手是**每个任务都会走**的路径

    它们最容易把"一个任务一个 loop"悄悄破坏掉：任何一个还写着
    `asyncio.run(...)`，任务里就又多了一个 loop，而症状（连接池作废、
    跨 loop 复用）不会立刻显现。因此这里用真实助手 + 真实临时库验证：
    **所有异步写入都跑在同一个 loop 上，并且真的落库了**。

    ⚠️ 必须是**同步**用例：本用例自己持有 `task_loop` 这个新 loop，
    而"在运行中的 loop 里再 run 另一个 loop"是不允许的 ——
    这正是 Celery 任务真实的样子（同步函数体 + 自己的 loop）。
    """

    def test_begin_and_mark_share_one_loop(self, test_db, monkeypatch):
        from types import SimpleNamespace

        from sqlalchemy import select

        from app.models.task_run import TaskRun, TaskStatus
        from app.tasks import common

        loops = []
        real_run_async = common.run_async

        def spy_run_async(coro, **kwargs):
            async def wrapper():
                loops.append(asyncio.get_running_loop())
                return await coro

            return real_run_async(wrapper(), **kwargs)

        monkeypatch.setattr(common, "run_async", spy_run_async)

        fake_task = SimpleNamespace(
            name="app.tasks.understand_tasks.understand_document_task",
            max_retries=2,
            request=SimpleNamespace(id="task-loop-e2e"),
        )

        async def fetch_run():
            async with test_db() as db:
                return (await db.execute(
                    select(TaskRun).where(TaskRun.task_id == "task-loop-e2e")
                )).scalars().first()

        with task_loop("unit_test") as loop:
            assert common.begin_task_run(fake_task, note_id=None) is True
            common.mark_task_succeeded(fake_task)
            run = run_async(fetch_run())

        assert len(loops) >= 2, "两次助手调用应当至少各跑一次协程"
        assert {id(x) for x in loops} == {id(loop)}, (
            "助手的异步写入没有跑在任务循环上 —— common.py 里还留着 asyncio.run"
        )
        assert run is not None, "任务记录没有落库（助手静默失败了）"
        assert run.status == TaskStatus.succeeded
        assert loop.is_closed()
