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
import re
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

# (正则, 每分钟上限, 规则名)
#
# ## 为什么是正则而不是"路径后缀"
#
# 原实现用 `path.endswith(suffix)` 匹配，规则表里写的是 `/understanding/start`
# 与 `/cleaning/start`。但真实路由是
#
#     POST /api/understanding/{note_id}/start
#     POST /api/cleaning/{note_id}/start
#
# —— **都不以 `/understanding/start` 结尾**。于是这两条规则从未生效过：
# 限流表看起来覆盖了"理解"与"清洗"，实际上它们是完全不限流的。
# 这类缺陷不会报错、不会进日志，只有真去数请求次数才会发现（本轮补的测试就是这么发现的）。
#
# 正则匹配让"带参数的路径"能被明确表达，也避免了下一个人再次写出一条永不命中的规则。
_RULES: Tuple[Tuple[str, int, str], ...] = (
    # 认证：登录/注册必然未认证，按 IP 计数（爆破防护）
    (r"^/api/auth/login$", 10, "login"),
    (r"^/api/auth/register$", 5, "register"),
    # 刷新/登出（阶段 6.3）：凭证在**请求体**里（刷新令牌），因此同样未认证、按 IP 计数。
    # 正常使用下刷新极低频（前端只在访问令牌过期时刷一次），60/分钟已经非常宽松；
    # 它挡的是"拿着泄露的刷新令牌脚本狂刷"这种形态（每次刷新都会写库）。
    # 登出更简单（一次更新），阈值给一半。
    (r"^/api/auth/refresh$", 60, "refresh"),
    (r"^/api/auth/logout$", 30, "logout"),

    # LLM 端点：每次调用都产生真实费用
    (r"^/api/understanding/[^/]+/start$", 10, "llm"),
    (r"^/api/understanding/[^/]+/generate-questions$", 10, "llm"),
    (r"^/api/knowledge/cards/[^/]+/generate-questions$", 10, "llm"),
    (r"^/api/understanding/ask$", 30, "llm"),
    (r"^/api/understanding/ask/stream$", 30, "llm"),
    # ⚠️ 笔记级的流式问答是**另一条**路径（不在 understanding 路由下）：
    # 覆盖检查发现它此前没有被任何规则命中 —— 这类"看起来覆盖了"的漏网
    # 正是把规则与真实路由对照（见 tests/test_rate_limit_coverage.py）才能发现的。
    (r"^/api/notes/[^/]+/ask/stream$", 30, "llm"),
    (r"^/api/assessment/compare$", 10, "llm"),
    (r"^/api/assessment/generate-quiz$", 10, "llm"),
    (r"^/api/graph/suggest$", 10, "llm"),
    (r"^/api/graph/suggest-semantic$", 10, "llm"),
    # 联合分析（资料 + 用户笔记）与拓展生成：都是一次完整的多轮 LLM 会话
    (r"^/api/knowledge/links/[^/]+/extract-combined$", 5, "llm"),
    (r"^/api/knowledge/cards/[^/]+/generate-extension$", 10, "llm"),
    # 复习提交：可能触发**语义判分**（一次 LLM 调用/题），因此与 LLM 同级限量。
    # 数量给得宽（一天几十道题是正常使用），但仍能挡住脚本刷题
    (r"^/api/review/submit$", 60, "llm"),
    (r"^/api/review/quick/[^/]+/submit$", 60, "llm"),
    (r"^/api/review/cards/[^/]+/submit$", 60, "llm"),

    # 任务类（消耗磁盘与算力，不直接花钱）
    (r"^/api/cleaning/[^/]+/start$", 10, "task"),
    # ⚠️ 真实路径是 `/api/upload/{note_id}/retry`（重跑转换），不是 notes 下的 retry。
    # 原规则写成 `/notes/{id}/retry` —— 一条**永不命中**的死规则（覆盖检查发现的）。
    (r"^/api/upload/[^/]+/retry$", 10, "task"),
    # 扫描导入：会遍历项目 source/ 目录并触发转换，属于重活
    (r"^/api/projects/[^/]+/scan$", 10, "task"),

    # 上传：大文件写入磁盘，且会触发转换
    (r"^/api/upload$", 30, "upload"),
    (r"^/api/upload/prepare$", 30, "upload"),
    (r"^/api/upload/commit$", 30, "upload"),
)

#: 编译一次（匹配在每个请求上发生，不该每请求重编译）
_COMPILED_RULES: Tuple[Tuple["re.Pattern[str]", int, str], ...] = tuple(
    (re.compile(pattern), limit, name) for pattern, limit, name in _RULES
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
    """匹配限流规则，返回 (上限, 规则名)

    只对 POST 限流：读接口的滥用由"分页 + 单写者"兜住，
    而把它们也纳入限流会让正常翻页的用户撞上 429（得不偿失）。
    """
    if method != "POST":
        return None
    for pattern, limit, name in _COMPILED_RULES:
        if pattern.match(path):
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
