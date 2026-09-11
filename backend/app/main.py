"""
FastAPI 应用入口模块

本模块是 EngramNote 后端服务的启动入口，负责创建和配置 FastAPI 应用实例。

主要职责：
- 定义应用生命周期管理（启动时初始化数据库）
- 配置 CORS 中间件（允许前端开发服务器跨域访问）
- 注册 API 路由（统一挂载到 /api 前缀下）
- 提供健康检查端点

设计决策：
- 使用 lifespan 上下文管理器替代 on_event 装饰器（FastAPI 推荐方式）
- CORS 仅允许开发服务器域名，生产环境应配置为实际前端域名
- 所有 API 路由统一挂载到 /api 前缀，便于反向代理和版本管理

Celery 启动说明（独立进程，不嵌入 FastAPI）：
- 启动 Celery Worker（处理异步任务）：
    celery -A app.tasks.celery_app worker --loglevel=info
- 启动 Celery Beat（定时任务调度器）：
    celery -A app.tasks.celery_app beat --loglevel=info
- Beat 必须作为独立进程运行，不能嵌入 FastAPI 进程，
  否则在多 worker 部署时会导致重复调度。
"""

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.router import api_router
from .config import get_settings
from .core import context
from .core.logging_config import setup_logging
from .core.tempfile_compat import apply_tempfile_compat
from .database import init_db
from .middleware.error_handler import ErrorHandlerMiddleware
from .middleware.rate_limit import RateLimitMiddleware
from .middleware.request_context import RequestContextMiddleware
from .services.llm_accounting_service import LLMQuotaExceeded

# 获取全局配置
settings = get_settings()
logger = logging.getLogger(__name__)


def _enforce_single_writer() -> None:
    """SQLite 路线下的单写者守卫（overhaul-plan 决策 D1=B / 附录 E.6 方案 A）

    ## 为什么必须显式拦住

    SQLite 全库只有一个写锁。`uvicorn --workers N`（N>1）会起 N 个**独立进程**，
    每个进程各有一个连接池，于是：

    - WAL 只允许"一写多读"，多进程写会在 `busy_timeout`（30 秒）耗尽后
      抛 `database is locked`。30 秒是很长的等待 —— 表现为请求莫名卡半分钟
      然后 500，而不是干脆失败。
    - 更隐蔽的是 `RateLimitMiddleware` 的滑动窗口、`tasks/common.py` 的
      引擎缓存都是**进程内**状态。多进程下每个进程各算各的，
      限流阈值实际变成 N 倍，看起来"限流没生效"。

    这类问题不会被单元测试发现（测试是单进程的），只会在部署后以
    "偶发 500 / 限流失效"的形态出现，排查成本极高。所以在启动时直接拒绝
    比事后调试便宜得多。

    ## 为什么是拒绝而不是自动降为 1

    静默把 N 改成 1 会让运维以为多进程部署成功了，实际只跑了一个 worker。
    宁可启动失败并说清楚原因，也不要一个"看起来正常但能力被悄悄削掉"的服务。

    Raises:
        RuntimeError: 使用 SQLite 且检测到多 worker 配置
    """
    import os

    from .database import _sqlite

    if not _sqlite():
        return

    raw = (
        os.environ.get("WEB_CONCURRENCY")
        or os.environ.get("UVICORN_WORKERS")
        or ""
    ).strip()
    if not raw:
        # 未显式配置：默认单进程（uvicorn 默认即 1），安全
        return

    try:
        workers = int(raw)
    except ValueError:
        raise RuntimeError(
            f"无法解析 worker 数量环境变量: WEB_CONCURRENCY={raw!r}。"
            "请设为整数 1，或改用 PostgreSQL。"
        ) from None

    if workers > 1:
        raise RuntimeError(
            f"检测到 {workers} 个 worker，但当前数据库是 SQLite。\n"
            "SQLite 全库只有一个写锁，多进程写会在 busy_timeout 耗尽后抛 "
            "database is locked；且限流/引擎缓存等进程内状态会各自为政"
            "（限流阈值实际放大为 N 倍）。\n"
            "请选择其一：\n"
            "  1) 固定单写者（本项目当前路线）：WEB_CONCURRENCY=1；\n"
            "  2) 迁移到 PostgreSQL（overhaul-plan 手术刀 1）。\n"
            "详见 docs/overhaul-plan.md 附录 E.6 与 docs/sqlite-single-writer.md。"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理

    在应用启动时执行初始化操作（如创建数据库表），
    yield 之后为应用关闭时的清理逻辑（当前无需清理）。

    Args:
        app: FastAPI 应用实例
    """
    # 启动时：运行环境兼容（替换 tempfile.mkdtemp，详见 core/tempfile_compat.py）
    apply_tempfile_compat()
    # 启动时：初始化日志配置
    setup_logging()
    # 启动时：拒绝会破坏 SQLite 单写者假设的部署配置
    _enforce_single_writer()
    logger.info("EngramNote 应用启动")
    # 启动时：初始化数据库，创建所有数据表
    await init_db()
    yield
    # 关闭时：释放共享 LLM HTTP 客户端，见 docs/decisions.md#F-05
    try:
        from .services.llm_service import close_llm_client
        close_llm_client()
        logger.info("EngramNote 应用关闭：已释放 LLM 共享客户端")
    except Exception as e:
        logger.warning(f"关闭 LLM 共享客户端失败: {e}")


# 创建 FastAPI 应用实例
app = FastAPI(
    title=settings.app_name,
    description="AI 驱动的学习笔记管理与知识库工具",
    version="0.1.0",
    debug=settings.debug,
    lifespan=lifespan,
)

# 配置 CORS 中间件 — 允许前端开发服务器跨域访问
# 生产环境应将 allow_origins 改为实际前端域名
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),  # 从配置解析（默认 Vite/CRA 本地端口）
    allow_credentials=True,    # 允许携带 Cookie
    allow_methods=["*"],       # 允许所有 HTTP 方法
    allow_headers=["*"],       # 允许所有请求头
)

# 注册全局异常处理中间件 — 捕获所有未处理异常，返回统一格式
# 顺序说明：Starlette 的 add_middleware 是**前插**语义，因此后注册的在外层。
# 实际执行顺序（外 → 内）为：
#     RequestContext → ErrorHandler → RateLimit → CORS → 路由
# 即 RequestContext 最先注入 request_id / user_id，限流器因此能读到已认证用户；
# ErrorHandler 在其内层，保证限流返回的 429 也走统一错误信封。
app.add_middleware(ErrorHandlerMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(RateLimitMiddleware)

# ---- 全局异常处理器：让所有 HTTP 错误响应体都携带 request_id ----
# 说明：路由层抛出的 HTTPException / RequestValidationError 由 FastAPI
# 内部的 ExceptionMiddleware 处理，不经过 ErrorHandlerMiddleware，
# 因此在这里注册处理器，保证 4xx/5xx 响应体统一为
# {"detail", "error_code", "request_id"} 格式，客户端可凭 request_id 定位日志。

def _error_payload(status_code: int, detail: str, error_code: str) -> dict:
    return {
        "detail": detail,
        "error_code": error_code,
        "request_id": context.get_request_id() or None,
    }


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    logger.warning(
        "HTTP 错误 | %s %s | status=%d | detail=%s",
        request.method, request.url.path, exc.status_code,
        exc.detail if isinstance(exc.detail, str) else str(exc.detail),
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_payload(
            exc.status_code,
            exc.detail if isinstance(exc.detail, str) else str(exc.detail),
            f"HTTP_{exc.status_code}",
        ),
        # 必须转发 exc.headers：否则路由里精心设置的
        # `headers={"WWW-Authenticate": "Bearer"}` 到不了客户端，
        # 401 响应会缺少 RFC 7235 要求的质询头，标准客户端无法据此重新认证。
        # 之前正是这里漏了一行，让 auth.py 里两处 401 的头形同虚设。
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning(
        "参数校验错误 | %s %s | detail=%s",
        request.method, request.url.path,
        str(exc).replace("\n", " ")[:500],
    )
    return JSONResponse(
        status_code=422,
        content=_error_payload(422, str(exc), "VALIDATION_ERROR"),
    )


# LLM 配额耗尽（阶段 4.3）
#
# 为什么单独注册处理器，而不是在路由里 try/except：
# LLM 调用散布在理解任务、问答、语义判分等多条路径上（其中一部分在
# Celery 任务里），逐个包 try 必然有漏。配额异常从 `llm_service` 统一抛出，
# 在这里转成稳定错误码，客户端只需认 `error_code`（不随文案变化）。
@app.exception_handler(LLMQuotaExceeded)
async def llm_quota_exceeded_handler(request: Request, exc: LLMQuotaExceeded):
    logger.warning(
        "LLM 配额耗尽 | %s %s | %s", request.method, request.url.path, exc.detail,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_payload(exc.status_code, exc.detail, exc.code),
    )


# 注册所有 API 路由，统一挂载到 /api 前缀下
app.include_router(api_router, prefix="/api")


@app.get("/health")
async def health_check():
    """
    健康检查端点

    用于监控和负载均衡器检测服务是否正常运行。
    不需要认证，返回应用名称和状态。

    Returns:
        dict: 包含 status 和 app 名称的字典
    """
    return {"status": "ok", "app": settings.app_name}
