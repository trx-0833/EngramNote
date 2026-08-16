"""
全局异常处理中间件模块
======================

统一的异常处理与错误响应出口。改造要点（可定位性增强）：

1. 所有错误响应统一携带 request_id（来自 core/context.py），客户端拿到
   报错后可直接向服务端提供 request_id，服务端按 rid=xxx 检索日志即可
   定位同请求的完整链路（含堆栈）。

2. 响应体统一格式：{"detail": str, "error_code": str, "request_id": str}；
   HTTPException 保留原始状态码，ValidationError 归为 422，
   未知异常归为 500 且不向客户端暴露内部细节。

3. 服务端日志：未知异常记录完整 traceback，并带上下文标签
   （rid/uid/tid 自动由 logging_config.ContextFormatter 注入），
   ERROR 级日志同时落入 errors.log 独立文件，定位不再大海捞针。

设计决策：
- 中间件挂在 CORS 内侧、RequestContextMiddleware 外侧
  （CORS → ErrorHandler → RequestContext → 路由），因此能捕获
  路由层及请求上下文中间件抛出的所有未处理异常。
- 不向客户端返回堆栈（安全），但服务端日志保留完整堆栈。
"""

import logging
from typing import Optional

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from ..core import context

logger = logging.getLogger(__name__)


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """
    全局异常处理中间件

    捕获所有未处理异常，返回统一格式的 JSON 响应（含 request_id）。
    区分已知异常（HTTPException、ValidationError）和未知异常。
    """

    def __init__(self, app: ASGIApp):
        super().__init__(app)

    @staticmethod
    def _error_response(status_code: int, detail: str, error_code: str) -> JSONResponse:
        """构造统一错误响应（自动附带当前 request_id）"""
        return JSONResponse(
            status_code=status_code,
            content={
                "detail": detail,
                "error_code": error_code,
                "request_id": context.get_request_id() or None,
            },
        )

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            response = await call_next(request)
            # 非 2xx 响应也补上 request_id 头（中间件顺序：本中间件在外层，
            # 内层 RequestContextMiddleware 已设置响应头，此处兜底）
            if response.status_code >= 400:
                response.headers.setdefault("X-Request-ID", context.get_request_id() or "")
            return response
        except HTTPException as exc:
            logger.warning(
                "HTTP 错误 | status=%d | detail=%s",
                exc.status_code, exc.detail,
            )
            return self._error_response(
                status_code=exc.status_code,
                detail=exc.detail if isinstance(exc.detail, str) else str(exc.detail),
                error_code=f"HTTP_{exc.status_code}",
            )
        except ValidationError as exc:
            logger.warning(
                "参数校验错误 | detail=%s",
                str(exc).replace("\n", " ")[:500],
            )
            return self._error_response(
                status_code=422,
                detail=str(exc),
                error_code="VALIDATION_ERROR",
            )
        except Exception as exc:  # noqa: BLE001 - 未知异常统一兜底
            # 完整堆栈进日志（含 rid/uid 上下文标签，errors.log 独立落盘）
            logger.error(
                "未处理异常 | %s %s | type=%s | detail=%s",
                request.method,
                request.url.path,
                type(exc).__name__,
                exc,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            return self._error_response(
                status_code=500,
                detail="服务器内部错误，请稍后重试",
                error_code="INTERNAL_ERROR",
            )
