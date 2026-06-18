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
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.router import api_router
from .config import get_settings
from .database import init_db
from .middleware.error_handler import ErrorHandlerMiddleware

# 获取全局配置
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理

    在应用启动时执行初始化操作（如创建数据库表），
    yield 之后为应用关闭时的清理逻辑（当前无需清理）。

    Args:
        app: FastAPI 应用实例
    """
    # 启动时：初始化数据库，创建所有数据表
    await init_db()
    yield


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
    allow_origins=["http://localhost:5173", "http://localhost:3000"],  # Vite 和 CRA 默认端口
    allow_credentials=True,    # 允许携带 Cookie
    allow_methods=["*"],       # 允许所有 HTTP 方法
    allow_headers=["*"],       # 允许所有请求头
)

# 注册全局异常处理中间件 — 捕获所有未处理异常，返回统一格式
app.add_middleware(ErrorHandlerMiddleware)

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
