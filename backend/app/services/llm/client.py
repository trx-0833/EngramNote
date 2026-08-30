"""共享 LLM HTTP 客户端基础设施

- 模块级共享 httpx.AsyncClient（进程内所有实例复用）
- 事件循环变化时自动重建客户端（Celery worker 每个任务新建 loop）
- 关闭共享客户端（应用关闭时调用，见 main.py lifespan）
- 日志脱敏辅助 _truncate_messages
"""

import asyncio
import logging
from typing import Dict, List, Optional

import httpx

from ...config import get_settings

logger = logging.getLogger("engramnote.llm")
settings = get_settings()


# ---- 模块级共享 LLM 基建，见 docs/decisions.md#F-05 ----
# 旧实现每次调用 `async with httpx.AsyncClient(...)` 新建客户端（连接池/TLS 全部浪费），
# 且 RateLimiter/Semaphore 为实例属性，多处 `LLMService()` 新建实例导致限流与并发
# 闸门完全不生效。以下提升为模块级单例，进程内所有实例共享。
#
# 注意（Event loop is closed 修复）：httpx 连接池中的连接绑定创建时的事件循环。
# Celery worker 中每个任务用 asyncio.run() 创建新事件循环，跨任务复用旧连接会报
# "Event loop is closed"。因此保存 client 创建时的 loop 对象，检测到 loop 变化时
# 自动重建 client（API 进程单 loop 不受影响）。
# 用 loop 对象引用比较（不用 id()：loop 被 GC 后 id 可能被新 loop 复用导致误判）。
_shared_llm_client: Optional[httpx.AsyncClient] = None
_shared_llm_client_loop: Optional[asyncio.AbstractEventLoop] = None


def _current_loop() -> Optional[asyncio.AbstractEventLoop]:
    """获取当前运行事件循环（无运行循环时返回 None）"""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def get_llm_client() -> httpx.AsyncClient:
    """获取（惰性创建）进程级共享 httpx 客户端；事件循环变化时自动重建"""
    global _shared_llm_client, _shared_llm_client_loop
    current_loop = _current_loop()
    if _shared_llm_client is not None and _shared_llm_client_loop is not current_loop:
        # 事件循环已变化（如 Celery 每个任务新建 loop）：旧连接全部失效，丢弃重建。
        # 不主动 aclose()：旧 loop 已关闭，调用 aclose 只会产生未 await 的 coroutine；
        # 连接随旧 loop/GC 释放（文件描述符由 OS 回收）。
        logger.warning(
            "检测到 LLM 客户端跨事件循环复用，重建共享客户端 "
            "(old_loop=%s, new_loop=%s)",
            _shared_llm_client_loop, current_loop,
        )
        _shared_llm_client = None
        _shared_llm_client_loop = None
    if _shared_llm_client is None:
        _shared_llm_client = httpx.AsyncClient(timeout=settings.llm_timeout_seconds)
        _shared_llm_client_loop = current_loop
    return _shared_llm_client


def close_llm_client() -> None:
    """关闭共享客户端（应用关闭时调用，见 main.py lifespan）"""
    global _shared_llm_client, _shared_llm_client_loop
    if _shared_llm_client is not None:
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_shared_llm_client.aclose())
            else:
                asyncio.run(_shared_llm_client.aclose())
        except Exception:
            pass
        _shared_llm_client = None
        _shared_llm_client_loop = None


def _truncate_messages(messages: List[Dict[str, str]], limit: int = 200) -> List[Dict[str, str]]:
    """日志脱敏：每条消息内容截断到 limit 字符（避免全量内容进日志，见 docs/decisions.md#F-20）"""
    result = []
    for m in messages:
        item = dict(m)
        content = item.get("content", "")
        if isinstance(content, str) and len(content) > limit:
            item["content"] = content[:limit] + f"...(截断,共{len(content)}字)"
        result.append(item)
    return result