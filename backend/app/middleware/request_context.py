"""
请求上下文中间件模块（Request Context Middleware）
====================================================

为每一个 HTTP 请求生成唯一 request_id，注入请求上下文（见 core/context.py），
并在响应头 X-Request-ID 中返回，实现「客户端报错 → 拿 request_id → 服务端
日志一键定位」的完整链路。同时记录请求开始/完成的结构化访问日志（含耗时、
状态码、用户ID），替代被抑制的 uvicorn.access 噪音日志。

主要职责：
- 生成/透传 request_id：优先使用客户端传入的 X-Request-ID（便于跨端追踪），
  否则生成 uuid4 hex
- 从 Authorization: Bearer <jwt> 中解析 user_id（仅用于日志关联，解析失败
  不影响请求处理，未登录请求 user_id 为空）
- 设置 contextvars 请求上下文，并确保请求结束（含异常路径）后重置
- 输出访问日志：请求开始（DEBUG）与请求完成（INFO，含状态码/耗时），
  4xx/5xx 分别以 WARNING/ERROR 级别输出，便于快速筛选异常请求
- 所有响应统一附带 X-Request-ID 头

设计决策：
- 使用 BaseHTTPMiddleware 挂在 ErrorHandlerMiddleware **外侧**
  （生效次序：RateLimit → RequestContext → CORS → ErrorHandler → 路由），保证：
  1) 错误响应同样携带 X-Request-ID（本中间件在响应回程上无条件设置该头，
     包括错误渲染器造出来的 4xx/5xx 与 CORS 自己应答的预检）；
  2) 4xx/5xx 按状态码进访问日志（WARNING/ERROR）—— 注意错误在到达本中间件
     之前**已经**被内层的 ErrorHandlerMiddleware 渲染成响应了，所以这里看到
     的是"带错误状态码的正常响应"，而不是异常；下面的 `except` 只是兜底
     （能逃出 ErrorHandler 的只剩 BaseException 一类）。
- 不改用纯 ASGI 中间件：contextvars 的注入/重置需要与请求严格配对，
  BaseHTTPMiddleware 的 finally 语义已经满足，且本中间件不读请求体。
- 用户解析失败（无效 Token）只影响日志关联字段，不拦截请求——
  认证本身由 /api/auth 依赖处理。
"""

import logging
import time
import uuid

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from ..core import context
from ..config import get_settings

logger = logging.getLogger("engramnote.access")
settings = get_settings()

# 请求体大小超过该值时不再读取（本中间件不读请求体，此处仅为文档/兜底）
_MAX_BODY_LOG_BYTES = 4096


def _extract_user_id(request: Request) -> str:
    """
    从 Authorization: Bearer <jwt> 中解析 user_id（仅用于日志关联）

    使用 jose 解码并校验签名/过期，解析失败返回空串（不影响请求）。

    阶段 6.3 起还要求令牌**是访问令牌**：刷新令牌（`typ=refresh`，有效期 30 天）
    放在这个头里同样能被解出 sub，于是日志会把一个"未通过认证的请求"
    记在某用户名下，限流键也会按该用户计数。判据复用
    `auth_service.is_access_token_payload`（含"旧令牌无 typ 视为访问令牌"的
    兼容规则），避免同一个判断在两处各写一遍而慢慢分叉。
    """
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        return ""
    token = auth[7:].strip()
    if not token:
        return ""
    try:
        from jose import jwt as jose_jwt

        from ..services.auth_service import is_access_token_payload

        payload = jose_jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
        if not is_access_token_payload(payload):
            return ""
        return str(payload.get("sub") or "")
    except Exception:
        return ""


class RequestContextMiddleware(BaseHTTPMiddleware):
    """
    请求上下文中间件

    为每个请求生成 request_id、解析 user_id，注入上下文并记录访问日志。
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # ---- 1. 生成/透传 request_id ----
        request_id = (request.headers.get("X-Request-ID") or "").strip()
        if not request_id or len(request_id) > 64 or not request_id.replace("-", "").isalnum():
            request_id = uuid.uuid4().hex
        # 客户端透传时保留原值，但同样做长度约束
        request_id = request_id[:64]

        method = request.method
        path = request.url.path
        user_id = _extract_user_id(request)

        # ---- 2. 注入上下文 ----
        context.set_request_context(
            request_id=request_id,
            user_id=user_id,
            method=method,
            path=path,
        )
        start = time.monotonic()

        logger.debug("请求开始 | %s %s", method, path)

        try:
            response = await call_next(request)
        except Exception:
            # 异常路径：记录访问日志（错误详情由 ErrorHandlerMiddleware 输出）
            elapsed_ms = (time.monotonic() - start) * 1000
            logger.error(
                "请求异常 | %s %s | 耗时 %.0fms",
                method, path, elapsed_ms,
            )
            raise
        finally:
            # 确保上下文不残留（contextvars 随 Task 自动清理，此处双保险）
            context.reset_context()

        # ---- 3. 访问日志（含状态码/耗时） ----
        elapsed_ms = (time.monotonic() - start) * 1000
        response.headers["X-Request-ID"] = request_id
        status_code = response.status_code
        log_line = "请求完成 | %s %s | status=%d | 耗时 %.0fms"
        if status_code >= 500:
            logger.error(log_line, method, path, status_code, elapsed_ms)
        elif status_code >= 400:
            logger.warning(log_line, method, path, status_code, elapsed_ms)
        else:
            logger.info(log_line, method, path, status_code, elapsed_ms)
        return response
