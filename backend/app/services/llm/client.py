"""共享 LLM HTTP 客户端基础设施

- 模块级共享 httpx.AsyncClient（进程内所有实例复用）
- 事件循环变化时自动重建客户端（Celery worker 每个任务新建 loop）
- 关闭共享客户端（应用关闭时调用，见 main.py lifespan）
- 日志脱敏辅助 _truncate_messages
"""

import asyncio
import json
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
            "检测到 LLM 客户端跨事件循环复用，重建共享客户端 (old_loop=%s, new_loop=%s)",
            _shared_llm_client_loop,
            current_loop,
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


class LLMGatewayError(RuntimeError):
    """LLM 网关返回 **4xx**（不可重试）时抛出的错误，消息里带响应体片段

    ## 为什么不让 `httpx.HTTPStatusError` 自己冒出去

    `str(httpx.HTTPStatusError)` 只有
    `Client error '400 Bad Request' for url '…'` —— 而网关把**真实原因**
    （`Model is unavailable.` / 参数被拒 / 会话缺失）写在 body 里。
    这条错误会一路冒到 API 响应，用户看到的就是

        生成拓展知识点失败: Client error '400 Bad Request' for url '…'

    这句话不指向任何可执行的动作。2026-11-07 的现场就是这样：真正的原因
    （配置里的模型已被上游下架）只躺在日志的 `body=` 字段里，而用户手上
    只有一个 400。（日志那一侧 4xx 分支早就打了 body —— 本类把同一句话
    也交给调用方。）

    ## 为什么保留 `status_code` / `body` / `model` 三个字段

    上层可能要按状态码分流（401 → 提示改密钥；400 → 提示改模型名），
    靠解析消息字符串判断是脆的。
    """

    def __init__(self, *, status_code: int, model: str, body: str, detail: Optional[str] = None) -> None:
        self.status_code = status_code
        self.model = model
        #: 响应体片段（原样，给日志/排查用）
        self.body = body
        #: 真正放进消息里给人看的那句：能解析出 `error.message` 时用它，否则回退 body
        self.detail = detail or body
        super().__init__(f"LLM 网关返回 {status_code}（不可重试，model={model}）: {self.detail}")


def _extract_upstream_message(text: str) -> Optional[str]:
    """从 OpenAI 风格的错误体里取出**人话**（`error.message` 之类）

    网关的两种典型形态：

        {"error": {"type": "server_error", "message": "Upstream request failed: Model is unavailable."}}
        {"error": {"type": "MissingSessionID"}}

    第一种把整段 JSON 塞进提示没有意义（用户要读的是那句话），
    第二种没有 `message`，就用 `type` 顶上；都取不到再回退原样片段。
    """
    if not text:
        return None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    err = data.get("error")
    if isinstance(err, dict):
        for key in ("message", "type", "code"):
            value = err.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    elif isinstance(err, str) and err.strip():
        return err.strip()
    for key in ("message", "detail"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def build_gateway_error(*, status_code: int, model: str, response) -> LLMGatewayError:
    """从 4xx 响应构造 `LLMGatewayError`（响应体为空/不可读时给出人话）

    显示用的那句话优先取网关 JSON 里的 `error.message`；取不到才用截断后的
    原始响应体（例如网关回的是 HTML 错误页）。
    """
    try:
        raw = response.text or ""
    except Exception:
        raw = ""
    body = _response_snippet(response, 300)
    if body == "<empty>":
        body = "响应体为空"
    elif body == "<unreadable>":
        body = "响应体不可读"
    return LLMGatewayError(
        status_code=status_code,
        model=model,
        body=body,
        detail=_extract_upstream_message(raw),
    )
