"""
pytest 共享 fixture（F-35）

提供：
- `test_db`：独立临时 SQLite 库（每次测试重建），不触碰真实 backend/data/db/engramnote.db
- `test_user_factory`：快速创建测试用户

用法：
    async def test_x(test_db):
        async with test_db() as session:
            ...

注意：需要 pytest-asyncio（pip install pytest-asyncio，见 requirements.txt）。
"""

import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture
def test_db():
    """
    独立临时 SQLite 测试库 fixture

    通过 DATABASE_URL 环境变量 + 重建 database 模块引擎实现隔离；
    测试结束自动 dispose 并删除临时库文件。
    """
    import asyncio
    import app.models  # noqa: F401 — 注册全部模型
    from app import database as db_mod

    tmp_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "tmp_test",
    )
    os.makedirs(tmp_dir, exist_ok=True)
    db_path = os.path.join(tmp_dir, f"test_{uuid.uuid4().hex[:8]}.db")

    old_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"

    from app.config import get_settings
    get_settings.cache_clear()
    db_mod.settings = get_settings()
    db_mod.database_url = db_mod.settings.get_database_url()
    db_mod._is_sqlite = db_mod.database_url.startswith("sqlite")
    db_mod.engine = db_mod.create_async_engine(
        db_mod.database_url,
        connect_args={"check_same_thread": False},
        echo=False,
    )
    if db_mod._is_sqlite:
        db_mod.register_sqlite_pragmas(db_mod.engine)
    # 关键：重建会话工厂，否则 async_session 仍绑定原始（真实）引擎
    db_mod.async_session = db_mod.async_sessionmaker(
        db_mod.engine,
        class_=db_mod.AsyncSession,
        expire_on_commit=False,
    )

    # 显式管理事件循环，避免 "no current event loop"（pytest 上下文无默认循环）
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(db_mod.init_db())
    finally:
        loop.close()

    yield db_mod.async_session

    cleanup_loop = asyncio.new_event_loop()
    try:
        cleanup_loop.run_until_complete(db_mod.engine.dispose())
    finally:
        cleanup_loop.close()
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass
    if old_url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = old_url


@pytest.fixture
def test_user_factory(test_db):
    """创建测试用户（返回 (user_id, email, password)）"""
    from app.models.user import User
    from app.services.auth_service import hash_password

    async def _factory(index: int = 1):
        email = f"user{index}@test.local"
        password = "TestPass123!"
        async with test_db() as session:
            user = User(
                id=str(uuid.uuid4()),
                email=email,
                username=f"user{index}",
                hashed_password=hash_password(password),
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user.id, email, password

    return _factory
