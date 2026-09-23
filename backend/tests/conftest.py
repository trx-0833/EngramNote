"""
pytest 共享 fixture（F-35）

提供：
- `test_db`：独立临时 SQLite 库（每次测试重建），不触碰真实 backend/data/db/engramnote.db
- `test_user_factory`：快速创建测试用户

**网络与生产数据守卫（见 docs/overhaul-plan.md §2.5 E-6）**：
本仓库的测试此前会在**收集阶段**就发真实 HTTP 请求 —— 实测
`pytest --collect-only` 直接烧掉真实 LLM 额度，并向 localhost:8001 发请求。
因此这里在 session 级安装守卫：默认**禁止任何外部网络访问**，
需要真实调用的用例必须显式标记 `@pytest.mark.integration` 并设置
`ENGRAMNOTE_ALLOW_NETWORK_TESTS=1`。

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


# ---------------------------------------------------------------------------
# 测试环境变量：必须在本文件被 import 后、任何 app 模块被 import 前设置
#
# ## 为什么需要在 conftest 里设，而不是靠本机 .env
#
# `app/config.py` 的 `Settings` 有一个生产环境校验：
#     debug=False 且 jwt_secret_key 为空 → 抛 ValidationError
# 而本机 `backend/.env` 里有 JWT_SECRET_KEY（该文件被 .gitignore 忽略），
# CI 上**没有**。于是 CI 上任何在模块层 `from app.config import get_settings`
# 的测试文件都会在 **收集阶段** import 失败：
#
#     ERROR tests/test_learning_metrics.py - ValidationError for Settings
#     Value error, 生产环境必须配置 JWT_SECRET_KEY
#     !!! Interrupted: 6 errors during collection !!!
#
# 症状极具误导性：报错像是"配置不正确"，实际是"测试**隐式依赖了本机 .env**"。
# 本机永远复现不了 —— 除非把 .env 移走。
#
# ## 为什么用 setdefault
#
# 用 `setdefault` 而不是直接赋值：本机若已显式配置了密钥（或 CI 通过
# workflow env 传了），以外部值为准，conftest 只做兜底。
#
# ## 这里的值只用于测试
#
# UUID + 固定前缀，不是任何真实环境使用的密钥；测试不签发对外有效的令牌
# （`tests/conftest.py` 的网络守卫同时阻断一切真实外呼）。
# ---------------------------------------------------------------------------

os.environ.setdefault(
    "JWT_SECRET_KEY",
    "test-only-jwt-secret-not-used-outside-tests-0123456789abcdef",
)


# ---------------------------------------------------------------------------
# 会话级数据库隔离：默认 DATABASE_URL 指向临时文件，绝不碰真实库
#
# ## 为什么需要（本轮 CI 实测）
#
# 只有一个**会话级**的默认值，才能保证"没接 `test_db` fixture 的用例"
# 也落到一个已建表的库上。此前的行为取决于环境：
#
#   本机：真实库 backend/data/db/engramnote.db 存在且已建表 → 用例静默读写真实库
#   CI  ：该文件不存在（被 .gitignore 忽略）→ SQLite 自动创建空文件
#         → 任何查询报 `no such table: users` → 500
#
# CI 上表现为 `test_rate_limit.py` 连续 10 次登录返回 500
# （实测日志：`未处理异常 ... no such table: users`），本机却全绿 ——
# 一个**只在 CI 复现**的失败。
#
# 修法：把这个默认值显式设到临时库，并在 pytest 启动时建表。
#   - 没接 fixture 的用例：落到会话临时库（已建表）→ 行为确定
#   - 接了 `test_db` 的用例：落到各自的独立临时库 → 互不干扰
#   - 真实库：任何情况下都不再被测试触碰
#
# `setdefault`：CI 或开发者若显式提供了 DATABASE_URL，以外部值为准。
# ---------------------------------------------------------------------------

import atexit  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402

_SESSION_DB_DIR = tempfile.mkdtemp(prefix="engramnote-test-session-")
_SESSION_DB_PATH = os.path.join(_SESSION_DB_DIR, "session.db")
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_SESSION_DB_PATH}")


def _cleanup_session_db() -> None:
    """进程退出时清理会话临时库（含 WAL 兄弟文件）"""
    shutil.rmtree(_SESSION_DB_DIR, ignore_errors=True)


atexit.register(_cleanup_session_db)


# ---------------------------------------------------------------------------
# 存储安全网：整个测试会话的 Vault 一律重定向到临时目录
#
# ## 为什么必须有（与上面的会话临时库同一个道理）
#
# `storage_service._get_storage_root()` 的默认值是**真实**的
# `backend/data/storage/`。只要有用例真的落盘（`test_purge_file_consistency.py`
# 这类"DB 与磁盘一致性"用例必然会），写入就发生在真实 Vault 里。
#
# 本仓库已经踩过这个坑：`tests/test_vault_audit.py:70-75` 记录了"事后删掉新增
# 顶层目录"的做法**根本不起作用** —— 磁盘侧枚举的是真实 Vault，清理只是补救。
# 更危险的是它依赖"运行期间没人往存储根写新目录"这个假设：用户本人往
# `data/storage/` 放一个新目录，跑一次测试就会被当成"测试新增"删掉。
#
# 因此这里做成**会话级**：不管哪个测试文件、有没有自己的 fixture，
# 本进程内 `get_vault_dir()` 永远指向临时目录。
# 真实用户数据（不可再生）不参与测试，这一点不能靠"每个文件自觉"。
#
# ## 与文件级重定向的关系
#
# `test_vault_audit.py` / `test_purge_file_consistency.py` 自己也会设
# `VAULT_DIR` 并重绑 `storage_service.settings`（更细的粒度：每个用例一个
# 干净 Vault）。它们保存/恢复的是**本 fixture 设下的值**，因此：
#   - fixture 顺序：本 fixture 先设（autouse + 更早实例化），文件级后设；
#   - 恢复时回到本会话的临时 Vault，而不是真实 Vault。
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _redirect_vault_to_tmp(tmp_path_factory):
    """把会话内所有落盘重定向到临时 Vault，绝不写 `backend/data/storage/`"""
    from app.config import get_settings
    from app.services import storage_service

    tmp_vault = tmp_path_factory.mktemp("vault")
    old_env = os.environ.get("VAULT_DIR")
    old_settings = storage_service.settings

    os.environ["VAULT_DIR"] = str(tmp_vault)
    get_settings.cache_clear()
    storage_service.settings = get_settings()

    yield tmp_vault

    if old_env is None:
        os.environ.pop("VAULT_DIR", None)
    else:
        os.environ["VAULT_DIR"] = old_env
    get_settings.cache_clear()
    storage_service.settings = old_settings


def _init_session_db() -> None:
    """在会话临时库上建表（`pytest_configure` 调用一次）

    幂等：表已存在时 `create_all` 是空操作（我们从不删表）。

    失败时**不抛异常**：建表失败只影响"没接 `test_db` fixture 的用例"，
    而那本该是异常路径；让整个会话起不来反而会掩盖真正的问题。
    """
    import asyncio

    try:
        import app.models  # noqa: F401 — 注册全部表定义
        from app import database as db_mod

        asyncio.run(db_mod.init_db())
    except Exception as exc:  # pragma: no cover - 取决于运行环境
        print(f"[conftest] 会话临时库建表失败（不影响接了 test_db 的用例）: {exc}")


# ---------------------------------------------------------------------------
# 网络安全守卫：默认阻断一切真实外呼
# ---------------------------------------------------------------------------

def _network_tests_allowed() -> bool:
    return os.environ.get("ENGRAMNOTE_ALLOW_NETWORK_TESTS", "").strip() in ("1", "true", "True")


class NetworkAccessBlocked(RuntimeError):
    """测试试图访问外网时抛出（默认策略）"""


_BLOCK_MESSAGE = (
    "测试禁止真实网络访问。若确需集成测试，请设置 ENGRAMNOTE_ALLOW_NETWORK_TESTS=1 "
    "并给用例加 @pytest.mark.integration 标记。"
)


_REAL_DB_PATH = os.path.abspath(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "data", "db", "engramnote.db")
)

#: 判定 SQL 是否为写操作的语句首关键字（用于真实库写入守卫）
_WRITE_KEYWORDS = (
    "insert", "update", "delete", "replace", "create", "drop", "alter",
    "truncate", "vacuum", "attach", "detach", "reindex",
)


#: 陈旧临时测试库的判定阈值（秒）。取 6 小时：足够旧到**不可能**属于另一个
#: 正在并发运行的 pytest 会话，又足以让残骸不会长期堆积。
_STALE_TMP_DB_AGE = 6 * 3600

#: 临时测试库目录（与 `test_db` fixture 用的是同一个）
_TMP_TEST_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "tmp_test",
)


def _sweep_stale_test_dbs(tmp_dir: str | None = None) -> int:
    """清理 `data/tmp_test/` 里的**陈旧**临时库，返回清理个数

    正常路径下 `test_db` fixture 会删掉自己的临时库（含两个引擎的句柄释放，
    见该 fixture 末尾）。但被强杀/崩溃的会话会留下文件，而它们**不会被再次使用**
    （文件名是随机 uuid），于是只能堆积。

    这与项目里反复出现的一类问题同源：**看不见的增长**（AF.10 的账本、
    `_backup/` 的保留策略）。区别是这里的增长发生在测试基础设施里，
    没有生产者会去调用清理函数，所以在会话启动时扫一次是最合适的落点。

    只删 6 小时以上的 `test_*.db` 及其**孤儿**侧车：

    - 新鲜的主库不删（可能是并发会话正在用的）；
    - 主库仍在的 `-wal` / `-shm` 不删（可能装着尚未 checkpoint 的数据）；
    - 主库**本次将被清理**的侧车一并删掉 —— 主库都没了，侧车没有意义。

    判定**不依赖 `os.listdir` 的顺序**：先算出本轮要删的主库集合，再据此判断
    侧车是否孤儿。否则"先删主库、后看侧车"会让同一份文件因遍历顺序不同而
    得到不同结果（本轮测试正是这样抓到第一版的顺序依赖）。

    Args:
        tmp_dir: 目标目录；缺省 `data/tmp_test/`（测试可注入，便于验证判定逻辑）
    """
    import time

    target = tmp_dir or _TMP_TEST_DIR
    if not os.path.isdir(target):
        return 0

    cutoff = time.time() - _STALE_TMP_DB_AGE

    def _stale(name: str) -> float | None:
        """返回陈旧文件的字节数；新鲜/读不到属性则返回 None"""
        try:
            stat = os.stat(os.path.join(target, name))
        except OSError:
            return None
        return stat.st_size if stat.st_mtime <= cutoff else None

    names = os.listdir(target)
    # 第一遍：本轮要清理的主库
    doomed_dbs = {
        name for name in names
        if name.startswith("test_") and name.endswith(".db") and _stale(name) is not None
    }

    # 第二遍：主库 + 孤儿侧车（"主库不在"或"主库本轮将被删"都算孤儿）
    removed = 0
    freed = 0
    for name in names:
        size = _stale(name)
        if size is None or not name.startswith("test_"):
            continue
        if name.endswith(".db"):
            pass  # 主库：陈旧即清理
        else:
            if not name.endswith((".db-wal", ".db-shm")):
                continue
            # ⚠️ 只去掉 "-wal" / "-shm" 四个字符，**不能**连 ".db" 一起去掉
            #    （`name[:-len(".db-wal")]` 会得到 "test_xxx" 而不是 "test_xxx.db"，
            #     于是每个侧车都被判成孤儿 —— 会把活库的 WAL 删掉，属于丢数据。
            #     本轮由 `test_removes_only_stale_test_dbs` 抓到。）
            base = name[:-4]
            if base not in doomed_dbs and os.path.exists(os.path.join(target, base)):
                continue  # 主库会活下来 → 侧车必须留着
        try:
            os.remove(os.path.join(target, name))
        except OSError:
            # 被占用的文件删不掉（Windows）：留给下一次会话，不报错
            continue
        removed += 1
        freed += size

    if removed:
        print(
            f"\n[conftest] 清理陈旧临时测试库: {removed} 个 / {freed / 1024 / 1024:.1f} MB"
        )
    return removed


def pytest_configure(config):
    """注册自定义 marker，并（默认）安装网络阻断 + 真实库写入守卫"""
    config.addinivalue_line("markers", "integration: 需要真实网络/外部 API 的用例（默认跳过）")
    config.addinivalue_line("markers", "slow: 耗时用例")

    _install_real_db_write_guard()
    _init_session_db()
    _sweep_stale_test_dbs()

    if _network_tests_allowed():
        return

    # 阻断 httpx：LLM 服务与大部分测试脚本走它
    try:
        import httpx

        # 进程内 / 本地测试宿主：这些地址不产生真实外呼。
        # - ASGITransport / WSGITransport / MockTransport 是显式的进程内传输
        # - TestClient 内部用 portal，拿不到 _transport，但它的 host 固定为
        #   "testserver"；localhost 系则是本机回环（开发服务器端口，不会有人真连）
        _LOCAL_HOSTS = {"testserver", "localhost", "127.0.0.1", "::1", "0.0.0.0"}

        def _is_in_process(client, request) -> bool:
            transport = getattr(client, "_transport", None)
            name = type(transport).__name__ if transport is not None else ""
            if "ASGI" in name or "WSGI" in name or "Mock" in name:
                return True
            try:
                return (request.url.host or "").lower() in _LOCAL_HOSTS
            except Exception:
                return False

        _orig_async_send = httpx.AsyncClient.send

        async def _guarded_async_send(self, request, *a, **kw):
            if _is_in_process(self, request):
                return await _orig_async_send(self, request, *a, **kw)
            raise NetworkAccessBlocked(f"{_BLOCK_MESSAGE} 目标: {request.url}")

        httpx.AsyncClient.send = _guarded_async_send

        _orig_sync_send = httpx.Client.send

        def _guarded_sync_send(self, request, *a, **kw):
            if _is_in_process(self, request):
                return _orig_sync_send(self, request, *a, **kw)
            raise NetworkAccessBlocked(f"{_BLOCK_MESSAGE} 目标: {request.url}")

        httpx.Client.send = _guarded_sync_send
    except Exception:  # pragma: no cover - httpx 缺失时无需守卫
        pass

    # 阻断 requests（部分历史测试脚本用它）
    try:
        import requests

        def _blocked_requests_send(self, request, *a, **kw):
            raise NetworkAccessBlocked(f"{_BLOCK_MESSAGE} 目标: {request.url}")

        requests.Session.send = _blocked_requests_send
    except Exception:  # pragma: no cover
        pass


def _install_real_db_write_guard() -> None:
    """**永久防线**：任何测试进程对真实库执行写语句时立刻失败

    为什么需要它：本轮实测发生过真实的数据泄漏 —— `test_task_runs.py` 里有
    两个用例当时没接 `test_db` fixture，它们经由 TestClient 注册的两个用户
    **写进了真实库**（`taskapi@example.com` 与 `taskapi+b51c0316@example.com`，
    已清理）。泄漏的机制是：`test_db` fixture 的 setup/teardown 同时承担
    "重置单例"的职责，不接它的用例会继承上一例留下的状态。

    这类泄漏的可怕之处在于**完全静默**：测试全绿，只有人去数生产库的行数
    才会发现。所以这里不再依赖"每个用例都记得加 fixture"这种约定，
    而是在 SQLAlchemy 的执行层拦截。

    为什么不用包装 `sqlite3.Connection`：它是 C 实现的不可变类型
    （`TypeError: cannot set '__init__' attribute of immutable type`）。
    `before_cursor_execute` 是引擎级事件，对同步与异步引擎都生效，
    且能拿到连接对应的数据库路径，正好够用。
    """
    try:
        from sqlalchemy import event
        from sqlalchemy.engine import Engine
    except Exception:  # pragma: no cover - 无 SQLAlchemy 时无需守卫
        return

    def _is_write(statement) -> bool:
        if not isinstance(statement, str):
            return False
        head = statement.lstrip().lstrip("(").lstrip().lower()
        return head.startswith(_WRITE_KEYWORDS)

    def _points_at_real_db(conn) -> bool:
        """该连接是否指向真实生产库"""
        try:
            url = str(conn.engine.url)
        except Exception:
            return False
        if not url.startswith("sqlite"):
            return False
        # 形如 sqlite+aiosqlite:///D:/engramnote/backend/data/db/engramnote.db
        path = url.split("///", 1)[-1]
        try:
            return os.path.abspath(path) == _REAL_DB_PATH
        except Exception:
            return False

    def _guard(conn, cursor, statement, parameters, context, executemany):
        if _is_write(statement) and _points_at_real_db(conn):
            raise RuntimeError(
                "测试试图写入真实生产库！\n"
                f"  库路径: {_REAL_DB_PATH}\n"
                f"  语句: {str(statement)[:200]}\n"
                "该用例很可能缺少 `test_db` fixture（它同时负责把数据库单例"
                "重置到临时库）。请给用例加上 `test_db` 参数。"
            )

    # 以 Engine 类为 target：pytest 插件阶段拿不到尚未创建的引擎实例，
    # 挂在类上即可覆盖此后新建的每一个引擎
    event.listen(Engine, "before_cursor_execute", _guard)


def pytest_collection_modifyitems(config, items):
    """自动识别需要真实网络的用例并（默认）跳过

    判定方式：读用例源码，若直接使用 `requests` / `httpx` 且未出现 `mock`
    则视为集成用例。这比人工逐个打标记可靠 —— 本仓库的历史脚本正是
    在**收集阶段**就发起真实请求，人工标记很容易漏。
    """
    import inspect

    network_marker = pytest.mark.integration
    for item in items:
        func = getattr(item, "function", None)
        if func is None:
            continue
        try:
            src = inspect.getsource(func)
        except (OSError, TypeError):
            continue
        uses_net = ("requests." in src or "httpx." in src or "urllib" in src)
        is_mocked = ("mock" in src.lower() or "monkeypatch" in src)
        if uses_net and not is_mocked:
            item.add_marker(network_marker)

    if _network_tests_allowed():
        return
    skip_marker = pytest.mark.skip(reason="integration 用例默认跳过（需 ENGRAMNOTE_ALLOW_NETWORK_TESTS=1）")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_marker)


def _reset_db_singletons() -> None:
    """清空配置与数据库单例缓存（测试隔离的核心操作）

    `database.py` 里 **engine 与 session factory 都是 `lru_cache` 单例**：

        get_db()  -> get_session_factory()  -> get_engine()

    因此只重绑定模块属性（`db.engine = ...`）**改不到 `get_db()`** —— 它每次
    请求都走 `get_session_factory()`，拿的是缓存里那个旧工厂。
    实测证据（本轮探针）：

        db.engine（模块属性）     -> 临时库
        get_engine()（lru_cache） -> 真实库      ← 不是同一个对象
        get_session_factory() 返回旧工厂 = True
        → FastAPI get_db 实际连接：真实库

    结论：**本文件的 test_db fixture 此前从未真正隔离过** ——
    `init_db()` 在临时库建表，而所有走 `get_db` 的 API 测试一直连的是
    真实生产库。清缓存才是唯一有效的做法。

    另外还要清两处自建引擎：
      - `app/tasks/common.py` 的 worker 侧引擎（模块级单例，非 lru_cache）
      - 上一条同样属于"import/首次调用时冻结数据库地址"这一类缺陷
    """
    from app.config import get_settings
    from app import database as db_mod

    get_settings.cache_clear()
    # 这两个才是真正的单例入口；清掉即让下一次访问按新配置重建
    db_mod.get_engine.cache_clear()
    db_mod.get_session_factory.cache_clear()
    _reset_task_sync_session()


def _reset_task_sync_session() -> None:
    """重置 Celery 任务侧的会话工厂（见 app/tasks/common.py::reset_sync_session）

    它是模块级普通单例（不是 lru_cache），只能通过显式函数清空。
    不重置的后果：任何走 `get_sync_session()` 的代码（task_run_service、
    update_note_status）在换库后仍写**真实库**。
    """
    import asyncio

    try:
        from app.tasks import common as task_common
    except Exception:  # pragma: no cover - 导入失败时无需重置
        return

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(task_common.reset_sync_session())
    finally:
        loop.close()


def _refresh_module_settings() -> None:
    """把各模块级 `settings` 引用重新指向缓存里的**同一个**新实例

    为什么必要：`app/` 下有 20 多个模块写着 `settings = get_settings()`
    （模块级名字，在 import 时求值）。`_reset_db_singletons()` 只是让
    `get_settings()` 下次返回新实例，**并不会**更新那些已经绑定的名字，
    于是它们继续拿着旧实例 —— 旧实例的 `database_url` 指向换库前的库。

    受影响最直接的正是 Celery 任务侧（`app/tasks/common.py`）：
    它的 `settings.get_database_url()` 决定引擎连哪个库。
    不刷新就会出现"临时库建表、任务侧写真实库"，
    与 database.py 那个"import 时冻结 database_url"的缺陷同源。

    这里统一刷新为同一个对象，避免"A 模块指向新实例、B 模块指向旧实例"
    这种更难排查的分叉。
    """
    from app.config import get_settings

    fresh = get_settings()
    for module_name in (
        "app.database",
        "app.tasks.common",
        "app.tasks.celery_app",
        "app.tasks.convert_tasks",
        "app.tasks.clean_tasks",
        "app.tasks.understand_tasks",
        "app.tasks.embedding_tasks",
        "app.tasks.reminder_tasks",
        "app.services.review_service",
    ):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "settings"):
            module.settings = fresh


@pytest.fixture
def test_db():
    """独立临时 SQLite 测试库 fixture

    通过「改 DATABASE_URL + 清空 settings/engine/session 单例缓存」实现**真隔离**：
    临时库建表、临时库读写、结束后删库并恢复单例。

    使用方式（与旧实现保持兼容）：
        async with test_db() as session:
            ...

    Yields:
        会话工厂（`async_sessionmaker`），与 `app.database.async_session` 等价
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

    # 清缓存 → 后续 get_engine()/get_session_factory()/get_db()/init_db()
    # 全部指向临时库
    _reset_db_singletons()
    db_mod.settings = db_mod.get_settings()
    _refresh_module_settings()

    # init_db() 内部使用模块级 `engine` 代理，会解析到刚重建的引擎
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(db_mod.init_db())
    finally:
        loop.close()

    yield db_mod.get_session_factory()

    # 释放临时库连接后再删除文件（Windows 上占用中的文件删不掉）。
    # 必须**两个引擎都释放**：database.py 的主引擎，以及 tasks/common.py
    # 的 worker 侧引擎。本轮实测：只释放前者时，后者仍持着文件句柄，
    # 导致 158 个临时库（约 54MB）在 data/tmp_test 下静默堆积。
    _reset_task_sync_session()
    cleanup_loop = asyncio.new_event_loop()
    try:
        cleanup_loop.run_until_complete(db_mod.get_engine().dispose())
    finally:
        cleanup_loop.close()

    # 删除临时库。注意 WAL 模式会额外产生 `-wal` / `-shm` 兄弟文件，
    # 只删主库会持续留下垃圾（实测泄漏过 24 个文件），因此三个都清。
    for suffix in ("", "-wal", "-shm"):
        p = db_path + suffix
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass

    # 恢复环境变量并**再次清缓存**，否则后续用例会继续用刚被删掉的临时库
    if old_url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = old_url
    _reset_db_singletons()
    db_mod.settings = db_mod.get_settings()
    _refresh_module_settings()


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
