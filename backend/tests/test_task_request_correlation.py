# -*- coding: utf-8 -*-
"""任务与请求的关联（`prid`，2026-11-08）

## 现场

一次"上传 → 转换 → 清洗 → 理解"的链路，在日志里是**两截**：

- API 侧：`[rid=abc uid=…] 请求完成 | POST /api/upload | status=200`
- Worker 侧：`[tid=xyz task=convert_document] 任务开始`

两截之间没有任何字段能对上 —— `note_id` 只在部分任务里作为 `biz=` 出现，
而它也不唯一（同一篇笔记会触发多个任务）。用户报"上传之后卡住了"时，
只能靠时间戳人工拼接。

## 做法

派发时把当前请求的 `request_id` 塞进 **Celery 消息头**（`headers=`），
worker 的 `task_prerun` 读到它并放进上下文，于是 worker 的每条日志都带 `prid=`：

    [tid=xyz task=convert_document prid=abc] 任务开始

用消息头而不是任务参数：**不用改 9 个任务函数的签名**（多传一个不存在的关键字
参数会让任务直接 TypeError），而且第三方任务也能带上。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core import context


@pytest.fixture(autouse=True)
def _clean_context():
    context.reset_context()
    yield
    context.reset_context()


class _FakeTask:
    """记录 `apply_async` 被怎么调用的假任务"""

    name = "fake.task"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def apply_async(self, **kwargs):  # noqa: ANN003 - 桩
        self.calls.append(kwargs)
        return "task-result"


# ── 派发侧 ────────────────────────────────────────────────────────────────


def test_dispatch_injects_parent_request_id_into_headers():
    """有请求上下文时，消息头里必须带上 `parent_request_id`"""
    from app.tasks.dispatch import dispatch

    context.set_request_context(request_id="rid-dispatch-1", user_id="u1", method="POST", path="/api/x")
    task = _FakeTask()

    assert dispatch(task, "note-1", backend="mineru") == "task-result"

    call = task.calls[0]
    assert call["args"] == ("note-1",), "位置参数被改动了"
    assert call["kwargs"] == {"backend": "mineru"}, "关键字参数被改动了"
    assert call["headers"]["parent_request_id"] == "rid-dispatch-1"


def test_dispatch_without_request_context_sends_no_header():
    """Celery 任务里再派发子任务时**没有** rid：不要伪造一个空 `prid`"""
    from app.tasks.dispatch import dispatch

    task = _FakeTask()
    dispatch(task, "note-1")

    assert "headers" not in task.calls[0] or not task.calls[0]["headers"]


def test_dispatch_keeps_caller_headers():
    """调用方自己带的 headers 不能被吞掉（例如自定义路由/重试策略）"""
    from app.tasks.dispatch import dispatch

    context.set_request_context(request_id="rid-2")
    task = _FakeTask()

    dispatch(task, headers={"x-trace": "t1"})

    headers = task.calls[0]["headers"]
    assert headers["x-trace"] == "t1"
    assert headers["parent_request_id"] == "rid-2"


# ── Worker 侧 ─────────────────────────────────────────────────────────────


def test_task_prerun_reads_parent_request_id_from_headers():
    """worker 开始任务时把 `prid` 放进上下文，日志标签里就能看到"""
    from app.tasks.celery_app import _task_prerun

    task = SimpleNamespace(
        name="app.tasks.convert_tasks.convert_document",
        request=SimpleNamespace(headers={"parent_request_id": "rid-from-request"}),
    )

    _task_prerun("tid-1", task)

    assert context.get_parent_request_id() == "rid-from-request"
    tag = context.context_tag()
    assert "prid=rid-from-request" in tag, tag
    assert "tid=tid-1" in tag, tag


def test_task_prerun_tolerates_missing_headers():
    """老版本消息（没有该头）不能让任务崩：prid 为空即可"""
    from app.tasks.celery_app import _task_prerun

    task = SimpleNamespace(name="t", request=SimpleNamespace(headers=None))
    _task_prerun("tid-2", task)

    assert context.get_parent_request_id() == ""
    assert "prid=" not in context.context_tag()


def test_context_dict_exposes_parent_request_id():
    """JSON 日志里叫 `parent_request_id`，文本标签里叫 `prid=`"""
    context.set_request_context(request_id="rid-a")
    context.set_task_context(task_id="tid-a", parent_request_id="rid-a")

    as_dict = context.context_dict()
    assert as_dict["request_id"] == "rid-a"
    assert as_dict["parent_request_id"] == "rid-a"
    assert as_dict["task_id"] == "tid-a"
