"""
数据库连接模块

本模块负责创建和管理 SQLAlchemy 异步数据库引擎与会话工厂，
为整个应用提供统一的数据库访问基础设施。

主要职责：
- 根据配置创建异步数据库引擎（支持 SQLite 和 PostgreSQL）
- 提供异步会话工厂 async_session
- 定义 ORM 声明基类 Base
- 提供 FastAPI 依赖注入函数 get_db()，用于在请求中获取数据库会话
- 提供 init_db() 函数，用于在应用启动时自动创建所有数据表

设计决策：
- 使用 aiosqlite 驱动支持 SQLite 异步操作
- SQLite 不支持连接池配置（pool_size/max_overflow），需条件判断
- SQLite 需要设置 check_same_thread=False 以支持多线程异步访问
- expire_on_commit=False 避免提交后属性过期，简化异步代码编写
- 开发模式使用 init_db() 自动建表，生产环境应使用 Alembic 迁移
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings

# 获取全局配置
settings = get_settings()

# ✅ 提示当前模式
if settings.debug:
    print("⚠️  当前处于 DEBUG 模式")

# 获取数据库连接 URL
database_url = settings.get_database_url()

# 判断是否为 SQLite 数据库（SQLite 需要特殊配置）
_is_sqlite = database_url.startswith("sqlite")

# SQLite 需要特殊配置：允许跨线程访问（默认 SQLite 只允许创建它的线程访问）
connect_args = {}
if _is_sqlite:
    connect_args = {"check_same_thread": False}

# 引擎配置参数
engine_kwargs = {
    "echo": settings.debug,  # 调试模式下输出 SQL 语句
    "connect_args": connect_args,
}
# SQLite 不支持 pool_size / max_overflow 参数，仅 PostgreSQL 需要
if not _is_sqlite:
    engine_kwargs["pool_size"] = 5       # 连接池保持的连接数
    engine_kwargs["max_overflow"] = 10   # 超出 pool_size 后允许的最大额外连接数

# 创建异步数据库引擎
engine = create_async_engine(database_url, **engine_kwargs)

# 创建异步会话工厂
# expire_on_commit=False: 提交后不自动过期对象属性，避免在异步上下文中出现懒加载问题
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """
    ORM 声明基类

    所有模型类均继承自此类，SQLAlchemy 通过它来跟踪所有模型与数据表的映射关系。
    Base.metadata 包含了所有模型的元信息，用于创建数据表等操作。
    """
    pass


async def get_db():
    """
    FastAPI 依赖注入：获取数据库会话

    使用 async with 确保会话在请求结束后正确关闭。
    通过 yield 将会话注入到路由处理函数中，请求结束后自动清理。

    Yields:
        AsyncSession: 异步数据库会话实例
    """
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """
    初始化数据库 — 创建所有表

    根据所有模型的 metadata 自动创建对应的数据表。
    此方法适用于开发环境快速启动，生产环境建议使用 Alembic 进行数据库迁移管理。
    使用 engine.begin() 确保建表操作在事务中执行。
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


if __name__ == "__main__":
    pass
