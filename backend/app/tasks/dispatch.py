"""
Celery 任务派发辅助（2026-11-08）
=================================

## 它解决的问题

一次"上传 → 转换 → 清洗 → 理解"在日志里是**两截**：

    [rid=abc uid=…] 请求完成 | POST /api/upload | status=200      ← API 侧
    [tid=xyz task=convert_document] 任务开始                        ← Worker 侧

两截之间没有任何字段能对上，用户报"上传之后卡住了"时只能靠时间戳人工拼接。

## 做法：消息头，而不是任务参数

派发时把当前请求的 `request_id` 放进 **Celery 消息头**（`headers=`），
worker 的 `_task_prerun` 读出来放进上下文，于是 worker 的每条日志都带 `prid=`：

    [tid=xyz prid=abc task=convert_document] 任务开始

为什么不用任务参数（`task.delay(note_id, parent_request_id=rid)`）：

1. 要让 9 个任务函数都加一个关键字参数，**任何一个漏了就 TypeError**；
2. 消息头是 Celery 自己的元数据通道，任务签名零改动；
3. 第三方/内置任务也能带上，不需要它知情。

## 用法

    from app.tasks.dispatch import dispatch

    dispatch(convert_document_task, note_id, object_name, source_type.value, backend=backend)

行为与 `task.delay(...)` 完全一致（位置参数进 `args`，关键字参数进 `kwargs`），
额外只在"当前有请求上下文"时补一个头。
"""

from __future__ import annotations

from typing import Any

from ..core import context


def dispatch(task: Any, *args: Any, **kwargs: Any) -> Any:
    """
    派发一个 Celery 任务，并带上发起请求的 `request_id`。

    Args:
        task: Celery 任务对象（`apply_async` 的持有者）
        *args: 任务的位置参数
        **kwargs: 任务的关键字参数；`headers=` 是 Celery 的选项，不传给任务函数

    Returns:
        Celery `AsyncResult`（与直接调用 `.delay()` 相同）

    注意：**没有请求上下文时不加任何头**（Celery 任务里再派发子任务就是这种情况），
    不伪造空值 —— 否则日志里会出现 `prid=` 后面什么都没有，反而误导。
    """
    headers = dict(kwargs.pop("headers", None) or {})
    request_id = context.get_request_id()
    if request_id:
        headers.setdefault("parent_request_id", request_id)

    options: dict[str, Any] = {}
    if headers:
        options["headers"] = headers
    return task.apply_async(args=args, kwargs=kwargs, **options)
