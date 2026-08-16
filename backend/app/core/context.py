"""
请求上下文模块（Request Context）
================================

通过 contextvars 将「请求级 / 任务级」的关联信息注入到整个调用链，
使日志系统与错误系统可以自动携带 request_id / user_id / task_id，
实现「一条报错 → 一个请求ID → 一键定位全部日志」的可观测性目标。

主要职责：
- 定义请求上下文变量：request_id（请求ID）、user_id（用户ID）、
  method/path（HTTP 方法/路径）、task_id（Celery 任务ID）
- 提供 set/reset/读取的辅助函数，供中间件、Celery 信号与业务代码调用
- 提供 context_dict() 将当前上下文转为 dict，供结构化日志使用

设计决策：
- 使用 contextvars（Python 3.7+ 标准库），异步安全：
  FastAPI 每个请求一个 Task，Celery 每个任务一个 Task，
  contextvars 天然按 Task 隔离，不会串号。
- 日志 Formatter 直接读取这些变量（见 logging_config.py），
  因此业务代码无需任何改动即可自动带上关联信息。
"""

from contextvars import ContextVar
from typing import Dict, Optional

# ---- 上下文变量定义（默认值 None / 空串） ----

# 请求 ID：一次 HTTP 请求的唯一标识（X-Request-ID 响应头同步返回）
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
# 用户 ID：从 JWT 解析出的用户标识（未登录为空）
user_id_var: ContextVar[str] = ContextVar("user_id", default="")
# HTTP 方法与路径（请求上下文用）
method_var: ContextVar[str] = ContextVar("method", default="")
path_var: ContextVar[str] = ContextVar("path", default="")
# Celery 任务 ID（任务上下文用）
task_id_var: ContextVar[str] = ContextVar("task_id", default="")
# Celery 任务名称（任务上下文用）
task_name_var: ContextVar[str] = ContextVar("task_name", default="")
# 业务关联 ID：如 note_id / project_id，由业务代码按需设置
biz_id_var: ContextVar[str] = ContextVar("biz_id", default="")


# ---- 读取辅助函数 ----

def get_request_id() -> str:
    """获取当前请求 ID（无则返回空串）"""
    return request_id_var.get()


def get_user_id() -> str:
    """获取当前用户 ID（未登录返回空串）"""
    return user_id_var.get()


def get_task_id() -> str:
    """获取当前 Celery 任务 ID（非任务上下文返回空串）"""
    return task_id_var.get()


def get_task_name() -> str:
    """获取当前 Celery 任务名称（非任务上下文返回空串）"""
    return task_name_var.get()


def get_biz_id() -> str:
    """获取当前业务关联 ID（如 note_id）"""
    return biz_id_var.get()


def context_dict() -> Dict[str, str]:
    """
    将当前上下文转为 dict（供结构化日志 / JSON 日志使用）

    Returns:
        dict: 仅包含已设置的字段（空值不出现）
    """
    result: Dict[str, str] = {}
    rid = get_request_id()
    uid = get_user_id()
    tid = get_task_id()
    tname = get_task_name()
    bid = get_biz_id()
    if rid:
        result["request_id"] = rid
    if uid:
        result["user_id"] = uid
    if tid:
        result["task_id"] = tid
    if tname:
        result["task_name"] = tname
    if bid:
        result["biz_id"] = bid
    method = method_var.get()
    path = path_var.get()
    if method and path:
        result["http"] = f"{method} {path}"
    return result


def context_tag() -> str:
    """
    将当前上下文转为可读的日志标签（用于控制台/文本日志）

    Returns:
        str: 形如 "[rid=abc123] [uid=user1] [tid=task-1]" 的标签串
    """
    parts = []
    rid = get_request_id()
    uid = get_user_id()
    tid = get_task_id()
    tname = get_task_name()
    bid = get_biz_id()
    if rid:
        parts.append(f"rid={rid}")
    if uid:
        parts.append(f"uid={uid}")
    if tid:
        parts.append(f"tid={tid}")
    if tname:
        parts.append(f"task={tname}")
    if bid:
        parts.append(f"biz={bid}")
    if not parts:
        return ""
    return "[" + " ".join(parts) + "]"


# ---- 写入辅助函数 ----

def set_request_context(request_id: str, user_id: str = "", method: str = "", path: str = "") -> None:
    """
    设置请求上下文（由 RequestContextMiddleware 在请求入口调用）

    Args:
        request_id: 请求 ID
        user_id: 用户 ID（未登录为空）
        method: HTTP 方法
        path: 请求路径
    """
    request_id_var.set(request_id)
    user_id_var.set(user_id)
    method_var.set(method)
    path_var.set(path)


def set_task_context(task_id: str, task_name: str = "", biz_id: str = "") -> None:
    """
    设置任务上下文（由 Celery 信号处理器在任务开始时调用）

    Args:
        task_id: Celery 任务 ID
        task_name: 任务名称
        biz_id: 业务关联 ID（如 note_id）
    """
    task_id_var.set(task_id)
    task_name_var.set(task_name)
    if biz_id:
        biz_id_var.set(biz_id)


def set_biz_id(biz_id: str) -> None:
    """设置业务关联 ID（如 note_id），便于在请求/任务内进一步定位"""
    biz_id_var.set(biz_id)


def reset_context() -> None:
    """
    重置全部上下文变量（由中间件 finally 与任务信号调用）

    注意：contextvars 在 Task 结束后会被自动丢弃，显式 reset 是为了
    防止 RequestContextMiddleware 与 ServerErrorMiddleware 之间的
    异常传播路径上残留脏数据。
    """
    for var in (
        request_id_var, user_id_var, method_var, path_var,
        task_id_var, task_name_var, biz_id_var,
    ):
        try:
            var.set("")
        except Exception:
            pass
