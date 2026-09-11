"""
verify 脚本共享的临时库引导（tmp db bootstrap）

## 为什么需要这个模块

`app/database.py` 里的 engine 与 session factory 是 **`lru_cache` 单例**：

    get_db()  ->  get_session_factory()  ->  get_engine()

历史上三个 `verify_fix_*.py` 脚本都这样"换库"：

    db_mod.database_url = db_mod.settings.get_database_url()
    db_mod._is_sqlite   = db_mod.database_url.startswith("sqlite")
    db_mod.engine       = db_mod.create_async_engine(db_mod.database_url, ...)

这是**无效的**：重绑定模块属性改不到 `get_engine()` 的缓存，
于是 `init_db()` 在临时库建表，而业务代码（走 `get_session_factory()`）
仍然连真实库。两个后果：

1. 验证脚本跑出来的结论是假的（读写发生在另一个库上）；
2. 更糟的是它**有真实库的写权限** —— 一次误操作就能改生产数据。
   （`database_url` / `_is_sqlite` 这两个模块级常量已在改造中移除，
   所以这些脚本现在会直接 AttributeError 崩掉，而不是静默写错库。）

## 正确做法

清掉 `get_settings` / `get_engine` / `get_session_factory` 三个缓存，
再重新取一次 —— 与 `tests/conftest.py::_reset_db_singletons` 完全一致。
唯一入口是 `bootstrap_temp_db()`。
"""

import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_SCRIPT_DIR)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


def _reset_singletons() -> None:
    """清空配置与数据库单例缓存（换库的唯一有效方式）"""
    from app import database as db_mod
    from app.config import get_settings

    get_settings.cache_clear()
    db_mod.get_engine.cache_clear()
    db_mod.get_session_factory.cache_clear()
    # database.py 里这些模块级引用也要跟着指向新 settings
    db_mod.settings = get_settings()


async def bootstrap_temp_db(tag: str = "verify") -> str:
    """把整套数据库单例指向一个新的临时库并建表，返回其路径

    必须是 async 的：调用方已经在 `asyncio.run(main())` 的 loop 里，
    再 `new_event_loop().run_until_complete()` 会报
    "Cannot run the event loop while another loop is running"。

    Args:
        tag: 临时库文件名前缀，便于区分是哪个脚本留下的文件

    Returns:
        str: 临时库文件路径

    Usage:
        db_path = await bootstrap_temp_db("p0")
        from app import database as db_mod
        async with db_mod.get_session_factory()() as session:
            ...
    """
    import app.models  # noqa: F401 — 注册全部模型到 Base.metadata

    from app import database as db_mod

    tmp_dir = os.path.join(_BACKEND_DIR, "data", "tmp_test")
    os.makedirs(tmp_dir, exist_ok=True)
    db_path = os.path.join(tmp_dir, f"{tag}.db")

    # WAL 模式会额外生成 -wal / -shm，三个都要清，否则残留旧数据
    for suffix in ("", "-wal", "-shm"):
        p = db_path + suffix
        if os.path.exists(p):
            os.remove(p)

    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    _reset_singletons()

    await db_mod.init_db()

    # 自检：确认所有入口真的指向临时库。
    # 这道检查不能省 —— 本模块存在的全部理由就是"换库曾经静默失败"。
    engine_url = str(db_mod.get_engine().url)
    factory_url = str(db_mod.get_session_factory().kw["bind"].url)
    if engine_url != factory_url:
        raise RuntimeError(
            "verify 脚本换库失败：engine 与 session factory 指向不同的库\n"
            f"  get_engine()          -> {engine_url}\n"
            f"  get_session_factory() -> {factory_url}"
        )
    if "tmp_test" not in engine_url.replace("\\", "/"):
        raise RuntimeError(
            f"verify 脚本换库失败：仍指向非临时库 {engine_url}。"
            "出于安全考虑拒绝继续运行（避免污染真实数据）。"
        )

    return db_path


async def teardown_temp_db(db_path: str) -> None:
    """释放连接并删除临时库（含 WAL 兄弟文件）"""
    from app import database as db_mod

    await db_mod.get_engine().dispose()

    for suffix in ("", "-wal", "-shm"):
        p = db_path + suffix
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
