"""
全局异常处理中间件模块

本模块提供统一的全局异常处理，捕获所有未处理的异常并返回统一格式的响应。

主要职责：
- 捕获 HTTPException，保留原始状态码和详情
- 捕获 RequestValidationError（422），格式化验证错误信息
- 捕获所有其他未处理异常，返回 500 内部服务器错误
- 记录未处理异常的日志，但不向用户暴露堆栈信息

设计决策：
- 中间件放在 CORS 之后、路由之前，确保所有请求都经过
- 返回统一格式：{"detail": str, "error_code": str}
- 未知异常只记录日志，不暴露内部信息
"""

import logging
import traceback

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from pydantic import ValidationError

logger = logging.getLogger(__name__)


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """
    全局异常处理中间件

    捕获所有未处理异常，返回统一格式的 JSON 响应。
    区分已知异常（HTTPException、ValidationError）和未知异常。
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            response = await call_next(request)
            return response
        except Exception as exc:
            # 尝试导入 HTTPException（避免循环导入）
            from fastapi import HTTPException

            if isinstance(exc, HTTPException):
                return JSONResponse(
                    status_code=exc.status_code,
                    content={
                        "detail": exc.detail,
                        "error_code": f"HTTP_{exc.status_code}",
                    },
                )

            if isinstance(exc, ValidationError):
                return JSONResponse(
                    status_code=422,
                    content={
                        "detail": str(exc),
                        "error_code": "VALIDATION_ERROR",
                    },
                )

            # 未知异常：记录日志但不暴露堆栈
            logger.error(
                f"未处理异常: {request.method} {request.url.path} — "
                f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            )
            return JSONResponse(
                status_code=500,
                content={
                    "detail": "服务器内部错误，请稍后重试",
                    "error_code": "INTERNAL_ERROR",
                },
            )
