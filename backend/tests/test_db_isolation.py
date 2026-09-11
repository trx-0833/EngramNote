"""
测试隔离的元测试（meta-test）

背景（见 docs/overhaul-plan.md §2.5 E-6 与本轮侦查）：
`tests/conftest.py` 的 `test_db` fixture **此前从未真正隔离过**。

原因：`app/database.py` 里 engine 与 session factory 都是 `lru_cache` 单例：

    get_db()  ->  get_session_factory()  ->  get_engine()

旧实现只重绑定模块属性（`db.engine = create_async_engine(...)`），
**改不到 `get_db()`** —— 它每次请求都走 `get_session_factory()`，
拿的是缓存里那个指向真实库的旧工厂。
实测（探针）：

    db.engine（模块属性）     -> 临时库
    get_engine()（lru_cache） -> 真实库      ← 不是同一个对象
    → FastAPI get_db 实际连接：真实库

后果：所有通过 `TestClient` 走 `get_db` 的 API 测试一直在**读写真实生产库**。
更糟的是这个缺陷**没有任何测试能发现** —— 本文件就是为了补上这道防线：
一旦隔离失效，这里的用例必须失败。
"""

import os

import pytest
from fastapi.testclient import TestClient


def _engine_url() -> str:
    from app import database as db_mod

    return str(db_mod.get_engine().url)


def test_outside_fixture_points_at_real_db():
    """fixture 之外，单例应指向真实生产库"""
    real = os.path.abspath(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "db", "engramnote.db")
    )
    url = _engine_url().replace("/", os.sep)
    assert "tmp_test" not in url, f"fixture 之外不应指向临时库: {url}"
    assert real.replace(os.sep, "/") in _engine_url().replace("\\", "/"), (
        f"fixture 之外应指向真实库，实际: {_engine_url()}"
    )


def test_engine_and_session_factory_are_consistent(test_db):
    """fixture 内：engine 与 session factory 必须指向**同一个**库

    这正是旧实现失效的点 —— 两者分叉时，建表在一个库、读写却在另一个库。
    """
    from app import database as db_mod

    engine_url = str(db_mod.get_engine().url)
    factory_engine_url = str(db_mod.get_session_factory().kw["bind"].url)

    assert engine_url == factory_engine_url, (
        f"engine 与 session factory 指向不同的库（隔离失效）:\n"
        f"  get_engine()            -> {engine_url}\n"
        f"  get_session_factory()   -> {factory_engine_url}"
    )


def test_fixture_uses_temp_db(test_db):
    """fixture 内应指向临时库，而不是真实库"""
    url = _engine_url()
    assert "tmp_test" in url, f"fixture 内应指向临时库，实际: {url}"


@pytest.mark.asyncio
async def test_api_sessions_use_temp_db(test_db):
    """**核心断言**：走 FastAPI `get_db` 拿到的会话也必须连临时库

    这条是整个文件的价值所在 —— 旧实现下它会失败，
    而那正是"API 测试在污染真实库"的直接证据。
    """
    import app.models  # noqa: F401 — 确保模型已注册
    from app.database import get_db
    from sqlalchemy import text

    # 直接在临时库里建一张哨兵表
    async with test_db() as session:
        await session.execute(text("CREATE TABLE _isolation_sentinel (v INTEGER)"))
        await session.execute(text("INSERT INTO _isolation_sentinel (v) VALUES (42)"))
        await session.commit()

    # 再通过 FastAPI 的依赖（get_db）取会话，验证它看到的是同一张表
    agen = get_db()
    session = await agen.__anext__()
    try:
        row = await session.execute(text("SELECT v FROM _isolation_sentinel"))
        assert row.scalar() == 42, (
            "FastAPI get_db 拿到的会话看不到临时库里的哨兵表 —— "
            "说明它连的不是临时库（测试隔离失效，正在读写真实库）"
        )
    finally:
        await agen.aclose()


def test_isolation_is_restored_after_fixture():
    """fixture 结束后单例必须恢复指向真实库

    否则后续用例会继续用已被删除的临时库 —— 表现为
    `sqlite3.OperationalError: no such table: users` 的 500。
    本用例依赖执行顺序：pytest 默认按文件中定义顺序运行，
    因此它排在使用了 fixture 的用例之后。
    """
    from app import database as db_mod

    url = str(db_mod.get_engine().url)
    assert "tmp_test" not in url, f"fixture 结束后应恢复真实库，实际: {url}"
    # session factory 也必须同步恢复，否则下一次 API 调用仍会打到临时库
    factory_url = str(db_mod.get_session_factory().kw["bind"].url)
    assert factory_url == url, (
        f"fixture 结束后 engine 与 session factory 不一致:\n"
        f"  engine -> {url}\n  factory -> {factory_url}"
    )


def test_client_requests_do_not_touch_real_db(test_db):
    """端到端：TestClient 发起的请求不应写入真实库

    用"真实库的 notes 行数在请求前后不变"来验证。
    仅做只读校验（GET /health 不需要认证），避免污染任何一侧。
    """
    import sqlite3

    from app.main import app

    real_db = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "db", "engramnote.db",
    )
    if not os.path.exists(real_db):
        pytest.skip("真实库不存在，跳过")

    def snapshot() -> tuple:
        con = sqlite3.connect(f"file:{real_db}?mode=ro", uri=True)
        try:
            return tuple(
                con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("notes", "quiz_items", "review_logs")
            )
        finally:
            con.close()

    before = snapshot()
    client = TestClient(app, client=("203.0.113.42", 9001))
    assert client.get("/health").status_code == 200
    after = snapshot()

    assert before == after, f"TestClient 请求改动了真实库: {before} -> {after}"
