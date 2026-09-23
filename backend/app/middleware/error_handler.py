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
- 本中间件是**最内层的用户中间件**：生效次序（外 → 内）为
      RateLimit → RequestContext → CORS → ErrorHandler → ExceptionMiddleware → 路由
  因此路由层（以及 ExceptionMiddleware 未接手的）一切未处理异常都落到这里。
- ⚠️ `CORSMiddleware` 必须紧贴本中间件**外侧**（main.py 的注册次序）。
  本中间件是"自己造响应"的中间件，而一个响应只会经过**外侧**中间件的 send
  包装 —— CORS 若在里侧，`AppError` 渲染出的响应就不带
  `Access-Control-Allow-Origin`（阶段 0.11 迁移后所有业务错误都走这条路，
  实测证据见 tests/test_cors_middleware_order.py）。
- 不向客户端返回堆栈（安全），但服务端日志保留完整堆栈。
"""

import logging

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from ..core import context
from ..core.app_error import AppError

logger = logging.getLogger(__name__)


# 无条件附加的安全响应头（2026-09-23 新增）
#
# ## 为什么放在模块级而不是类里
#
# `_error_response` 是 `@staticmethod`，它的异常分支直接 `return` 响应、
# **不会**经过 `dispatch` 里那段统一补全（`call_next` 根本没被调用）。
# 因此错误响应必须由它自己带头；而静态方法读不到类属性之外的实例状态，
# 把常量放在模块级最直接，也让"错误路径"和"正常路径"共用同一份定义。
#
# ## 为什么放在这个中间件里（正常路径）
#
# 它是**最内层**的用户中间件，所有响应（成功 / 错误 / 由内层
# ExceptionMiddleware 渲染的 4xx）都要穿过它 —— 放在这一层才能保证
# "一个都不漏"。此前项目只有 CORS 与两个自定义中间件，
# 没有任何安全响应头（`main.py` 的注册段可核）。
#
# ## 逐条依据
#
# - `X-Content-Type-Options: nosniff`：本项目会返回用户上传转换出的
#   Markdown/HTML 片段，MIME 嗅探是这类内容变成 XSS 载体的经典路径。
# - `X-Frame-Options: DENY` + CSP 的 `frame-ancestors 'none'`：
#   禁止被 iframe 嵌套，防点击劫持（笔记应用里全是"删除/归档"这类按钮）。
# - `Referrer-Policy`：笔记 URL 里可能含 note_id，跨站跳转时不外泄。
# - `Content-Security-Policy` 只设 `frame-ancestors` 与 `base-uri`：
#   本服务同时是 **API**（返回 JSON）与（dev 下的）/docs 页面宿主，
#   加一整套 CSP 会连带影响 FastAPI 文档页与前端 dev server，
#   属于"需要单独评估"的改动。这里只设**不依赖资源来源**的两条，
#   它们是纯收益且无兼容风险。
# - 刻意**不**设 `Strict-Transport-Security`：本项目默认以 http 本地
#   运行（README 的快速开始就是 http://localhost:5173），
#   HSTS 会把这个 http 站点在浏览器里锁成 https，等于让本地开发不可用。
#   生产走 https 时应在**反代**（nginx）层加，那里才知道自己是不是 https。
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": "frame-ancestors 'none'; base-uri 'self'",
}


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """
    全局异常处理中间件

    捕获所有未处理异常，返回统一格式的 JSON 响应（含 request_id）。
    区分已知异常（HTTPException、ValidationError）和未知异常。
    """

    def __init__(self, app: ASGIApp):
        super().__init__(app)

    @staticmethod
    def _error_response(
        status_code: int, detail: str, error_code: str, data=None
    ) -> JSONResponse:
        """构造统一错误响应（自动附带当前 request_id，可选附带 data）

        ⚠️ 这里必须**自己**附加安全响应头：异常分支是直接 `return` 出去的，
        不会经过 `dispatch` 里那段统一的头部补全（`call_next` 根本没被调用）。
        漏掉这一步的后果是"错误响应缺 X-Content-Type-Options / X-Frame-Options"，
        而错误响应恰恰是最容易被诱导渲染的一类。
        """
        content = {
            "detail": detail,
            "error_code": error_code,
            "request_id": context.get_request_id() or None,
        }
        if data is not None:
            content["data"] = data
        return JSONResponse(
            status_code=status_code, content=content, headers=dict(SECURITY_HEADERS)
        )

    #: 无条件附加的安全响应头（2026-09-23 新增）
    #:
    #: ## 为什么放在这个中间件里
    #:
    #: 它是**最内层**的用户中间件，所有响应（成功 / 错误 / 由内层
    #: ExceptionMiddleware 渲染的 4xx）都要穿过它 —— 放在这一层才能保证
    #: "一个都不漏"。此前项目只有 CORS 与两个自定义中间件，
    #: 没有任何安全响应头（`main.py` 的注册段可核）。
    #:
    #: ## 逐条依据
    #:
    #: - `X-Content-Type-Options: nosniff`：本项目会返回用户上传转换出的
    #:   Markdown/HTML 片段，MIME 嗅探是这类内容变成 XSS 载体的经典路径。
    #: - `X-Frame-Options: DENY` + CSP 的 `frame-ancestors 'none'`：
    #:   禁止被 iframe 嵌套，防点击劫持（笔记应用里全是"删除/归档"这类按钮）。
    #: - `Referrer-Policy`：笔记 URL 里可能含 note_id，跨站跳转时不外泄。
    #: - `Content-Security-Policy` 只设 `frame-ancestors` 与 `base-uri`：
    #:   本服务同时是 **API**（返回 JSON）与（dev 下的）/docs 页面宿主，
    #:   加一整套 CSP 会连带影响 FastAPI 文档页与前端 dev server，
    #:   属于"需要单独评估"的改动。这里只设**不依赖资源来源**的两条，
    #:   它们是纯收益且无兼容风险。
    #: - 刻意**不**设 `Strict-Transport-Security`：本项目默认以 http 本地
    #:   运行（README 的快速开始就是 http://localhost:5173），
    #:   HSTS 会把这个 http 站点在浏览器里锁成 https，等于让本地开发不可用。
    #:   生产走 https 时应在**反代**（nginx）层加，那里才知道自己是不是 https。
    SECURITY_HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Content-Security-Policy": "frame-ancestors 'none'; base-uri 'self'",
    }

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            response = await call_next(request)
            # 非 2xx 响应也补上 request_id 头（中间件顺序：本中间件在外层，
            # 内层 RequestContextMiddleware 已设置响应头，此处兜底）
            if response.status_code >= 400:
                response.headers.setdefault("X-Request-ID", context.get_request_id() or "")
            for header, value in self.SECURITY_HEADERS.items():
                response.headers.setdefault(header, value)
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
        except AppError as exc:
            logger.warning(
                "业务错误 | code=%s | status=%d | detail=%s",
                exc.code, exc.http_status, exc.message,
            )
            return self._error_response(
                status_code=exc.http_status,
                detail=exc.message,
                error_code=exc.code,
                data=exc.data,
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
