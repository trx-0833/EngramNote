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


# ---- OpenCode 网关会话头（x-opencode-session） ----
# opencode.ai 的网关要求每个 chat/completions 请求携带 x-opencode-session 头，
# 缺失时直接返回 400 MissingSessionID（实测：加头后同请求立即 200）。
# 会话标识用于网关路由与计费归集，因此必须**跨请求稳定**，不能在每次调用时新建：
# LLMService 是"每次业务调用 new 一个"的用法（见 rag_service.answer_question），
# 把 session 放在实例上等于每请求换一个会话，失去复用意义。
#
# 与 httpx client 同理，按事件循环保存：Celery 每个任务用 asyncio.run() 新建 loop，
# 若跨 loop 复用同一 session 字符串无副作用（它只是字符串），但按 loop 隔离可让
# 「一次任务内稳定、任务之间不串味」的语义更清晰，也便于将来换成真实会话对象。
_shared_session_id: Optional[str] = None
_shared_session_id_loop: Optional[asyncio.AbstractEventLoop] = None


def get_opencode_session_id() -> str:
    """获取（惰性创建）当前事件循环内的 OpenCode 会话标识；事件循环变化时重建

    仅对 opencode.ai 网关有意义，但无条件返回一个稳定值也无害，
    因此调用方无需关心 provider 判断（见 is_opencode_gateway）。
    """
    global _shared_session_id, _shared_session_id_loop
    current_loop = _current_loop()
    if _shared_session_id is not None and _shared_session_id_loop is not current_loop:
        _shared_session_id = None
        _shared_session_id_loop = None
    if _shared_session_id is None:
        import uuid

        _shared_session_id = str(uuid.uuid4())
        _shared_session_id_loop = current_loop
    return _shared_session_id


def is_opencode_gateway(base_url: str) -> bool:
    """判断 base_url 是否指向需要 x-opencode-session 的 OpenCode 网关"""
    return "opencode.ai" in (base_url or "").lower()


def build_llm_headers(api_key: str, base_url: str) -> Dict[str, str]:
    """构造 LLM 请求头（含 OpenCode 网关所需的会话头）"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if is_opencode_gateway(base_url):
        headers["x-opencode-session"] = get_opencode_session_id()
    return headers


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


def _response_snippet(response, limit: int = 500) -> str:
    """提取 HTTP 错误响应体片段用于日志诊断

    网关的错误原因（鉴权、限流、参数、会话缺失）通常只在响应体里，
    只记录 str(exc) 会丢失这些信息。截断避免超长 HTML 错误页污染日志。
    """
    try:
        text = response.text or ""
    except Exception:
        return "<unreadable>"
    text = text.replace("\n", " ").replace("\r", " ").strip()
    if not text:
        return "<empty>"
    return text[:limit] + ("...(截断)" if len(text) > limit else "")