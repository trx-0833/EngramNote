"""
HTTP 入口限流中间件（进程内滑动窗口，零外部依赖）

背景（见 docs/overhaul-plan.md §2.5 E-1 / §2.9 B2）：
本项目此前**没有任何 HTTP 层限流** —— 全仓唯一的限流器是
`services/llm/rate_limit.py` 的进程内令牌桶，且它的语义是「排队等待」而非拒绝，
还被所有用户共享。结果是：

- `/api/auth/login` 可无限次调用 → 配合 6 位密码策略可在线爆破
- 所有调用 LLM 的端点（理解 / 出题 / 问答 / 评估 / 图谱推断）无配额 →
  一个账号即可脚本化烧光 API 额度，并把全局 10 RPM 桶占满，
  导致其他用户的 LLM 功能被拖到排队超时（跨租户可用性打击）

设计取向：
- **零外部依赖**：不引入 Redis / slowapi。当前部署是单进程 uvicorn，
  进程内计数足够；将来水平扩展时应替换为 Redis 实现（接口保持不变）。
- **只保护敏感端点**：不按路径正则全量限流，避免误伤轮询类接口
  （前端有 5 秒轮询任务状态的逻辑）。
- **超额返回 429 并带 Retry-After**，而不是静默排队 —— 排队会把
  "限流"变成"超时"，用户与运维都看不出真实原因。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Deque, Dict, Optional, Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..core import context

logger = logging.getLogger(__name__)

# 窗口与阈值。保守取值：正常用户手工操作远达不到这些上限，
# 但足以让脚本化爆破/刷接口在几十秒内被挡住。
WINDOW_SECONDS = 60

# (方法, 路径后缀) -> (每分钟上限, 规则名)
# 用「路径后缀匹配」而非全量正则，语义直观且不会误伤。
_RULES: Tuple[Tuple[str, str, int, str], ...] = (
    ("POST", "/auth/login", 10, "login"),
    ("POST", "/auth/register", 5, "register"),
    # LLM 端点：每次调用都产生真实费用
    ("POST", "/understanding/start", 10, "llm"),
    ("POST", "/generate-questions", 10, "llm"),
    ("POST", "/ask", 30, "llm"),
    ("POST", "/ask/stream", 30, "llm"),
    ("POST", "/assessment/compare", 10, "llm"),
    ("POST", "/assessment/generate-quiz", 10, "llm"),
    ("POST", "/graph/suggest", 10, "llm"),
    ("POST", "/graph/suggest-semantic", 10, "llm"),
    ("POST", "/cleaning/start", 10, "task"),
    ("POST", "/upload/prepare", 30, "upload"),
)


def _client_key(request: Request, rule: str) -> str:
    """构造限流键：优先用已认证用户，否则退回客户端 IP

    认证优先的理由：同一 NAT 后的多个用户不应互相拖累；
    而登录/注册必然未认证，只能用 IP。
    """
    user_id: Optional[str] = None
    try:
        user_id = context.get_user_id()
    except Exception:
        user_id = None
    if user_id:
        return f"u:{user_id}:{rule}"
    client = request.client.host if request.client else "unknown"
    return f"ip:{client}:{rule}"


class _SlidingWindow:
    """进程内滑动窗口计数器（线程安全）"""

    def __init__(self) -> None:
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def reset(self) -> None:
        """清空全部计数（测试隔离用；运维手动解除限流时也可调用）"""
        with self._lock:
            self._hits.clear()

    def check(self, key: str, limit: int, now: float) -> Tuple[bool, int]:
        """记录一次命中并判断是否超限

        Returns:
            (allowed, retry_after_seconds)
        """
        cutoff = now - WINDOW_SECONDS
        with self._lock:
            bucket = self._hits.setdefault(key, deque())
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                # 最早一次命中滑出窗口即可重试
                retry_after = max(1, int(bucket[0] + WINDOW_SECONDS - now) + 1)
                return False, retry_after
            bucket.append(now)
            # 顺手清理长期空桶，避免无界增长
            if len(self._hits) > 10000:
                for k in [k for k, v in self._hits.items() if not v]:
                    self._hits.pop(k, None)
            return True, 0


_window = _SlidingWindow()


def _match_rule(method: str, path: str) -> Optional[Tuple[int, str]]:
    """匹配限流规则，返回 (上限, 规则名)"""
    if method != "POST":
        return None
    for rule_method, suffix, limit, name in _RULES:
        if rule_method == method and path.endswith(suffix):
            return limit, name
    return None


class RateLimitMiddleware(BaseHTTPMiddleware):
    """敏感端点限流中间件（超额返回 429）"""

    async def dispatch(self, request: Request, call_next):
        matched = _match_rule(request.method, request.url.path)
        if matched is None:
            return await call_next(request)

        limit, rule = matched
        key = _client_key(request, rule)
        allowed, retry_after = _window.check(key, limit, time.monotonic())
        if not allowed:
            logger.warning(
                "限流触发 | rule=%s | key=%s | limit=%d/%ds | path=%s",
                rule, key, limit, WINDOW_SECONDS, request.url.path,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "detail": f"请求过于频繁，请在 {retry_after} 秒后重试",
                    "error_code": "RATE_LIMITED",
                    "request_id": context.get_request_id() or None,
                },
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)
