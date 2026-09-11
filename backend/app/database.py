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

from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

import logging

from .config import get_settings

# 获取全局配置
settings = get_settings()
logger = logging.getLogger(__name__)

# 提示当前模式
if settings.debug:
    logger.warning("当前处于 DEBUG 模式")

# 注意：这里**不缓存** database_url / _is_sqlite 到模块级常量。
#
# 原实现是 `database_url = settings.get_database_url()`（import 时求值一次），
# 而 `get_engine()` 读的是这个冻结字符串。后果是：**运行期改配置无效** ——
# 即使清空 `get_settings` / `get_engine` 的 lru_cache，重建出的引擎仍然指向
# import 时那一个路径。测试隔离因此完全失效（详见 tests/test_db_isolation.py
# 与 docs/overhaul-plan.md §2.5 E-6）：临时库建了表，但所有 API 请求读写的
# 都是真实生产库。
#
# 现在改为在使用处即时求值（`_db_url()` / `_sqlite()`），语义与
# `_rebuild_dangling_tables` 中既有的做法一致（那里早已显式用
# settings.get_database_url() 以适配测试重建 settings 的场景）。


def _db_url() -> str:
    """即时解析数据库连接 URL（尊重运行期配置变更与缓存清理）"""
    return settings.get_database_url()


def _sqlite() -> bool:
    """即时判断是否为 SQLite 方言"""
    return _db_url().startswith("sqlite")


@lru_cache
def get_engine():
    """
    懒加载创建异步数据库引擎

    仅首次调用时构建引擎并注册 SQLite 连接级 PRAGMA；模块 import 时
    不会创建引擎，也不会触碰数据库目录。lru_cache 即 once-guard：
    同一进程内引擎只构建一次，PRAGMA 只注册一次。

    配置热切换：调用方在改变数据库配置后需 `get_engine.cache_clear()`
    （测试的 test_db fixture 即如此），本函数会在下次调用时按新配置重建。
    """
    is_sqlite = _sqlite()

    # SQLite 需要特殊配置：允许跨线程访问（默认 SQLite 只允许创建它的线程访问）
    connect_args = {}
    if is_sqlite:
        connect_args = {"check_same_thread": False}

    # 引擎配置参数
    engine_kwargs = {
        "echo": settings.debug,  # 调试模式下输出 SQL 语句
        "connect_args": connect_args,
    }
    # SQLite 不支持 pool_size / max_overflow 参数，仅 PostgreSQL 需要
    if not is_sqlite:
        engine_kwargs["pool_size"] = 5       # 连接池保持的连接数
        engine_kwargs["max_overflow"] = 10   # 超出 pool_size 后允许的最大额外连接数

    engine = create_async_engine(_db_url(), **engine_kwargs)
    if is_sqlite:
        register_sqlite_pragmas(engine)
    return engine


@lru_cache
def get_session_factory():
    """
    懒加载创建异步会话工厂

    仅首次调用时构建，绑定 get_engine() 返回的单例引擎。
    expire_on_commit=False: 提交后不自动过期对象属性，避免在异步上下文中出现懒加载问题。

    Returns:
        async_sessionmaker: 异步会话工厂（单例）
    """
    return async_sessionmaker(
        get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )


# ---- SQLite 连接级 PRAGMA：逐连接开启外键、WAL 与 busy_timeout（见 docs/decisions.md#F-07） ----
# SQLite 默认不启用外键约束（PRAGMA foreign_keys 默认 OFF），导致模型上
# ON DELETE CASCADE 全部失效、删除笔记/卡片遗留孤儿数据。foreign_keys 与
# busy_timeout 均为连接级设置，须在每个新连接建立时执行（不能在事务内切换）。
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """每个 SQLite 新连接建立时启用外键、WAL 日志模式与写锁超时"""
    try:
        cursor = dbapi_connection.cursor()
        # 启用外键约束，让 ON DELETE CASCADE 真正生效
        cursor.execute("PRAGMA foreign_keys=ON")
        # WAL 日志模式：默认的 DELETE 模式下写事务会阻塞全部读事务，
        # 而本项目是「API + Celery worker + Celery beat」三进程共享同一个
        # SQLite 文件（见 docker-compose.yml），并发写必然触发
        # 'database is locked'。WAL 允许读写并发，是消除该问题的前提。
        # journal_mode 是**数据库文件级**的持久设置（不是连接级），写入一次即
        # 对后续所有连接生效；此处放在 connect 钩子里是为了兼容首次建库的连接。
        cursor.execute("PRAGMA journal_mode=WAL")
        # 写锁等待 30 秒：LLM 相关写事务可跨越外部 API 调用（config.llm_timeout_seconds
        # 默认 600s），5 秒过短会直接抛 'database is locked'。
        cursor.execute("PRAGMA busy_timeout=30000")
        # NORMAL 同步级别：WAL 下仍保证崩溃一致性，但避免每次提交都 fsync，
        # 显著降低长任务链路的写延迟。
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()
    except Exception:
        # 非 SQLite 方言或驱动不支持时忽略
        pass


def register_sqlite_pragmas(target_engine) -> None:
    """为引擎注册 SQLite 连接级 PRAGMA（供主引擎与测试复用）"""
    from sqlalchemy import event

    event.listen(target_engine.sync_engine, "connect", _set_sqlite_pragma)


class _LazyProxy:
    """延迟解析代理：import 时不触发底层对象创建，属性访问/调用时才解析"""

    def __init__(self, resolver):
        self._resolver = resolver

    def __getattr__(self, name):
        return getattr(self._resolver(), name)

    def __call__(self, *args, **kwargs):
        return self._resolver()(*args, **kwargs)


# 模块级 engine / async_session 保持 import 兼容：任何 `from ..database import
# engine / async_session` 的调用点在首次真正使用时才经代理解析为懒加载单例。
engine = _LazyProxy(get_engine)
async_session = _LazyProxy(get_session_factory)


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

    会话作用域是**整个请求**（FastAPI 的 yield 依赖在响应结束后才收尾），
    请求结束后自动清理。

    ## 事务边界（阶段 1′ 第 2 项）

    SQLAlchemy 是 **autobegin** 语义：session 上第一次执行语句就会开启事务，
    直到 `commit()` / `rollback()` / `close()`。因此本函数刻意显式
    `commit()` 与 `rollback()`，而不是依赖 `close()` 的隐式回滚：

    - 路由自行 commit 后，本函数再 commit 一次是**空操作**（没有活动事务），
      不改变语义；
    - 路由**忘了** commit 时，本函数补一次提交，避免"HTTP 200 但数据没落库"
      这种最难排查的静默失败；
    - 路由抛异常时显式回滚，语义清晰，且不依赖驱动实现细节。

    ## 已知限制：流式响应期间的会话存活

    用 `StreamingResponse`（SSE）的端点在**整个流式输出期间**持有这个
    session，因为依赖的收尾发生在响应体发送完毕之后。若该端点在此前
    发生过写操作且未提交，写锁会被持有数十秒。

    实测（`scripts/_audit_long_tx.py` 的 AST 审计）当前代码库**没有**
    "写后 commit 前夹慢操作"的位置，因此暂不引入更复杂的会话管理；
    但**新增流式端点时必须遵守**：在 `yield` 第一个事件之前完成所有写操作
    并 `await db.commit()`。

    Yields:
        AsyncSession: 异步数据库会话实例
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        # 路由抛出的异常在此被记录，用于 `finally` 中区分"正常收尾"与"异常收尾"
        route_failed = False
        try:
            yield session
        except Exception:
            route_failed = True
            # 显式回滚（不依赖 close() 的隐式行为）
            try:
                if session.in_transaction():
                    await session.rollback()
            except Exception:  # pragma: no cover - 回滚失败时不再掩盖原异常
                logger.warning("请求异常后的回滚失败", exc_info=True)
            raise
        finally:
            # 兜底提交：覆盖"路由忘了 commit"的情况（HTTP 200 但数据没落库
            # 是最难排查的一类静默失败）。无活动事务时是空操作。
            #
            # 三点必须注意：
            # 1. 放在 `finally` 而不是 `try` 之后 —— 生成器被 `aclose()` 收尾时
            #    触发的是 `GeneratorExit`，它继承自 **BaseException**，
            #    不会被 `except Exception` 捕获，因此 `try` 之后的代码不会执行
            #    （本轮实测踩到：兜底提交形同虚设）。
            # 2. 路由已失败时不再提交 —— 否则会把异常路径的半截数据落库。
            # 3. 提交自身失败必须回滚并**吞掉异常**，否则会用提交失败
            #    掩盖路由原本的真实错误，让排查方向完全错位。
            if not route_failed:
                try:
                    if session.in_transaction():
                        await session.commit()
                except Exception:
                    logger.warning("请求收尾时的兜底提交失败，已回滚", exc_info=True)
                    try:
                        await session.rollback()
                    except Exception:  # pragma: no cover
                        pass
            await session.close()


async def init_db():
    """
    初始化数据库 — 创建缺失的表（**绝不修改或删除已有数据**）

    启动路径只做两件安全的事：
    1. create_all() —— 只创建不存在的表，不触碰已有表
    2. _migrate_sqlite() —— 补加缺失的列 / 防御性建表 / 只读孤儿检查（仅报告）

    **破坏性 schema 变更（重建悬挂引用表）与破坏性数据清理（全局关系去重）
    已移出启动路径**，改为需显式设置 ENGRAMNOTE_ALLOW_DESTRUCTIVE_MIGRATION=1
    才执行（见 _destructive_migration_allowed 与 _rebuild_dangling_tables）。

    原因：这两个操作原先在**每个进程启动时**无条件执行，而本项目同时运行
    API / Celery worker / Celery beat 三个进程。任何一次中断（断电、OOM、Ctrl-C）
    落在 DROP TABLE 与 RENAME 之间，就会造成用户知识图谱数据丢失。
    启动路径必须是无损的，这是数据安全底线。
    """
    # 必须显式导入模型包：`Base.metadata` 只包含**已被导入**的模型的表，
    # 而本模块从不导入 `app.models`。此前全靠调用方的导入链"碰巧"把模型
    # 注册进来（main.py 经由路由间接导入）。后果是：任何直接调用
    # `init_db()` 的场景（独立脚本、Celery worker 首次启动）在
    # `Base.metadata` 为空的情况下执行 create_all —— 静默地什么都不建，
    # 直到业务代码查询时才报 no such table。
    # 本轮实测踩到：新增 task_runs 表后，一次性迁移脚本没能建出该表。
    from . import models  # noqa: F401 — 副作用导入，注册全部表定义

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # 对 SQLite：检查并补充已有表缺失的列（只加不删，安全）
        if _sqlite():
            await _migrate_sqlite(conn)

        # FTS5 全文索引（阶段 2.5′）：虚拟表不在 Base.metadata 里，
        # 需单独创建。放在这里而不是 `_migrate_sqlite` 内部，是因为它
        # **不是列迁移**，且要能独立于列迁移被理解与测试。
        if _sqlite():
            await _ensure_fts_index(conn)

    # 清理两阶段上传遗留的超时临时目录（与 DB 后端无关，启动时兜底执行）
    cleanup_stale_uploads()


async def _ensure_fts_index(conn) -> None:
    """创建 FTS5 全文索引虚拟表（阶段 2.5′，幂等）

    ## 设计要点

    **外部内容表**（`content='chunks'`）：FTS 只存倒排索引，正文按列名
    从 `chunks` 读。好处是正文不存两份 —— 两份就有不一致的可能，
    而"索引与正文不一致"是最难发现的一类检索缺陷。

    `content_rowid='chunk_rowid'`：必须指向 `chunks` 的**整数**主键。
    不能写 `'rowid'` —— `BaseModel` 的 VARCHAR 主键会被 SQLite 当成
    rowid 的别名，于是两边行号类型不同、JOIN 静默返回 0 条
    （实测踩到，见 `models/chunk.py`）。

    **索引 `grams` 列而不是 `content`**：SQLite 内置的 `trigram` 分词器
    实测更差（严格 Recall@5 57.84% vs 60.21%），而 FTS5 没有内置中文
    bigram 分词器。因此切词在写入侧完成、结果存进 `chunks.grams`，
    FTS 用内置 `unicode61`（按空白切）索引它。

    **不做触发器**：`content=` 模式下的触发器需要额外的 delete/insert 命令表，
    且"触发器没配对"会静默地让索引与正文漂移。改为在
    `chunk_service.index_note_chunks` 写入后显式同步，并提供
    `fts_search_service.rebuild_all` 兜底 —— 显式同步更容易验证。

    FTS5 是编译期选项，精简的 SQLite 构建可能没有。此时**不报错**，
    只记一条 warning：词法检索会退化为 Python BM25，而不是整体不可用。
    """
    from sqlalchemy import text as _text

    def _create(sync_conn):
        try:
            # 旧定义可能指向错误的 content_rowid（本项目早期版本写成 'rowid'，
            # 对 VARCHAR 主键表无效）。检测到不一致就重建 ——
            # 否则 'rebuild' 会因找不到该列而失败，且失败被 except 吞掉后
            # 表现为"索引建好了但查不出东西"。
            existing = sync_conn.execute(_text(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='chunks_fts'"
            )).fetchone()
            if existing and "chunk_rowid" not in (existing[0] or ""):
                sync_conn.execute(_text("DROP TABLE chunks_fts"))
                existing = None
            table_existed = existing is not None

            sync_conn.execute(_text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5("
                "  grams,"
                "  content='chunks',"
                "  content_rowid='chunk_rowid',"
                "  tokenize='unicode61'"
                ")"
            ))
            # 索引只在**新建时**重建一次。
            #
            # 为什么不能每次都 rebuild：`init_db()` 在每个进程启动时都跑，
            # 而测试套件会创建上百个临时库 —— 每次都全量重建会让整个套件
            # 从 70 秒膨胀到 190 秒（实测）。稳态下索引由
            # `chunk_service.index_note_chunks` 增量维护，不需要在这里重建。
            #
            # 外部内容表的索引必须用官方 'rebuild' 命令建立。
            # **不能**用 `INSERT INTO fts(rowid, col) SELECT ...` ——
            # 那只写倒排索引、不认内容表，查询会返回 0 条（实测踩到）。
            if not table_existed:
                sync_conn.execute(_text(
                    "INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')"
                ))
        except Exception as exc:  # pragma: no cover - 取决于 SQLite 构建
            logger.warning(
                "FTS5 不可用或索引重建失败，词法检索将退化为 Python BM25: %s", exc
            )

    await conn.run_sync(_create)


def _bigrams_for_migration(text_value: str) -> str:
    """迁移用的 bigram 切词（与 `fts_search_service.to_bigrams` 同一口径）

    刻意不 import 服务模块：`database.py` 是被所有模块依赖的最底层模块，
    让它在迁移路径上反向依赖 services 会形成循环导入风险。
    代价是同一算法有两处实现 —— 因此有测试断言两者结果一致
    （`test_fts_search.py::test_migration_and_service_bigrams_agree`）。
    """
    cleaned = "".join(ch for ch in (text_value or "") if not ch.isspace())
    if len(cleaned) < 2:
        return cleaned
    return " ".join(cleaned[i:i + 2] for i in range(len(cleaned) - 1))


def _destructive_migration_allowed() -> bool:
    """是否允许执行破坏性 schema 迁移（默认禁止）

    破坏性操作 = DROP/重建业务表 + 全局 DELETE。它们只在**明确的升级场景**下
    由运维显式触发，绝不能随进程启动自动发生。设置环境变量
    ENGRAMNOTE_ALLOW_DESTRUCTIVE_MIGRATION=1 即可放行。
    """
    import os

    return os.environ.get("ENGRAMNOTE_ALLOW_DESTRUCTIVE_MIGRATION", "").strip() in ("1", "true", "True")


def cleanup_stale_uploads(max_age_hours: int = 24) -> None:
    """
    清理两阶段上传遗留的超时临时目录

    prepare 阶段暂存的文件若未 commit（如用户放弃上传），会残留在
    data/tmp/upload/{uuid}/ 下。本函数在应用启动时兜底删除超过
    max_age_hours 的临时目录；commit 成功后的即时清理仍由 upload API 负责。

    Args:
        max_age_hours: 临时目录允许存活的时长（小时），默认 24
    """
    from datetime import datetime, timedelta

    import shutil

    from .config import TMP_UPLOAD_DIR

    if not TMP_UPLOAD_DIR.is_dir():
        return
    cutoff = datetime.now() - timedelta(hours=max_age_hours)
    cleaned = 0
    for entry in TMP_UPLOAD_DIR.iterdir():
        if not entry.is_dir():
            continue
        try:
            mtime = datetime.fromtimestamp(entry.stat().st_mtime)
            if mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                cleaned += 1
        except OSError:
            continue
    if cleaned > 0:
        logger.info("临时上传清理: 删除 %d 个超时目录", cleaned)


async def _migrate_sqlite(conn):
    """
    SQLite 简易迁移：检查并添加已有表缺失的列

    create_all() 只创建不存在的表，不会对已有表做 ALTER TABLE。
    此函数检查模型定义的列与实际表的列差异，自动添加缺失列。
    仅适用于开发环境的简易迁移，生产环境应使用 Alembic。

    Args:
        conn: 异步数据库连接（engine.begin() 上下文中的连接）
    """
    from sqlalchemy import inspect, text

    def _do_migrate(sync_conn):
        inspector = inspect(sync_conn)
        table_names = inspector.get_table_names()

        # 检查并创建 projects 表（防御性建表，对应 Alembic 005 迁移）
        # 需在 notes.project_id 列添加之前执行，避免 ALTER REFERENCES 找不到目标表
        if 'projects' not in table_names:
            sync_conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id VARCHAR NOT NULL PRIMARY KEY,
                    user_id VARCHAR NOT NULL REFERENCES users(id),
                    name VARCHAR(200) NOT NULL,
                    description TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
                )
                """
            ))
            sync_conn.execute(text(
                "CREATE INDEX ix_projects_user_id ON projects (user_id)"
            ))
            logger.info("SQLite 迁移: 已创建 projects 表")

        # 检查 notes 表是否缺少列
        if 'notes' in table_names:
            existing_columns = {col['name'] for col in inspector.get_columns('notes')}
            if 'folder_id' not in existing_columns:
                sync_conn.execute(text(
                    "ALTER TABLE notes ADD COLUMN folder_id VARCHAR REFERENCES folders(id)"
                ))
                logger.info("SQLite 迁移: 已为 notes 表添加 folder_id 列")
            if 'note_role' not in existing_columns:
                sync_conn.execute(text(
                    "ALTER TABLE notes ADD COLUMN note_role VARCHAR DEFAULT 'material' NOT NULL"
                ))
                logger.info("SQLite 迁移: 已为 notes 表添加 note_role 列")
            # 回收站软删除标记（对应 Alembic 009）
            if 'trashed_at' not in existing_columns:
                sync_conn.execute(text(
                    "ALTER TABLE notes ADD COLUMN trashed_at DATETIME"
                ))
                logger.info("SQLite 迁移: 已为 notes 表添加 trashed_at 列")
            try:
                sync_conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_notes_trashed_at ON notes (trashed_at)"
                ))
            except Exception:
                # 索引已存在时忽略
                pass

        # 检查并创建 note_projects 标签关联表（防御性建表，对应项目标签化重构）
        # 项目从 Vault 第一层目录演化为纯标签后，笔记与项目为多对多关系，承载于此表
        if 'note_projects' not in table_names:
            sync_conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS note_projects (
                    id VARCHAR NOT NULL PRIMARY KEY,
                    note_id VARCHAR NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                    project_id VARCHAR NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    user_id VARCHAR NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    UNIQUE (note_id, project_id)
                )
                """
            ))
            sync_conn.execute(text(
                "CREATE INDEX ix_note_projects_note_id ON note_projects (note_id)"
            ))
            sync_conn.execute(text(
                "CREATE INDEX ix_note_projects_project_id ON note_projects (project_id)"
            ))
            sync_conn.execute(text(
                "CREATE INDEX ix_note_projects_user_id ON note_projects (user_id)"
            ))
            logger.info("SQLite 迁移: 已创建 note_projects 表")

        # 检查并创建 note_material_links 表（防御性建表，正常情况下 create_all 已创建）
        # 端点可空 + ON DELETE SET NULL：物理删除笔记时悬挂保留双链记录（回收站策略）
        if 'note_material_links' not in table_names:
            sync_conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS note_material_links (
                    id VARCHAR NOT NULL PRIMARY KEY,
                    user_id VARCHAR NOT NULL REFERENCES users(id),
                    personal_note_id VARCHAR REFERENCES notes(id) ON DELETE SET NULL,
                    material_note_id VARCHAR REFERENCES notes(id) ON DELETE SET NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    UNIQUE (personal_note_id, material_note_id)
                )
                """
            ))
            logger.info("SQLite 迁移: 已创建 note_material_links 表")

        # 检查并创建 note_annotations 表（防御性建表，正常情况下 create_all 已创建）
        if 'note_annotations' not in table_names:
            sync_conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS note_annotations (
                    id VARCHAR NOT NULL PRIMARY KEY,
                    user_id VARCHAR NOT NULL REFERENCES users(id),
                    note_id VARCHAR NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                    view_mode VARCHAR(20) NOT NULL,
                    type VARCHAR(20) NOT NULL,
                    text_content TEXT NOT NULL,
                    context_before TEXT DEFAULT '' NOT NULL,
                    context_after TEXT DEFAULT '' NOT NULL,
                    color VARCHAR(20),
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
                )
                """
            ))
            logger.info("SQLite 迁移: 已创建 note_annotations 表")

        # 检查并创建 note_versions 表（防御性建表，对应 Alembic 004 迁移）
        if 'note_versions' not in table_names:
            sync_conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS note_versions (
                    id VARCHAR NOT NULL PRIMARY KEY,
                    note_id VARCHAR NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                    user_id VARCHAR NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    version_number INTEGER NOT NULL,
                    source VARCHAR(20) NOT NULL,
                    content_size INTEGER NOT NULL,
                    change_summary TEXT,
                    storage_path VARCHAR NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
                )
                """
            ))
            sync_conn.execute(text(
                "CREATE INDEX ix_note_versions_note_id ON note_versions (note_id)"
            ))
            sync_conn.execute(text(
                "CREATE INDEX ix_note_versions_user_id ON note_versions (user_id)"
            ))
            logger.info("SQLite 迁移: 已创建 note_versions 表")

        # note_versions (note_id, version_number) 唯一索引（见 docs/decisions.md#F-31）
        # （防并发重号；create_version 捕获 IntegrityError 重试）
        if 'note_versions' in table_names:
            try:
                sync_conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_note_versions_note_version "
                    "ON note_versions (note_id, version_number)"
                ))
            except Exception as e:
                logger.warning(f"创建 note_versions 唯一索引失败（忽略）: {e}")

        # 检查并创建 learning_goals 表（防御性建表，对应 Alembic 004 迁移）
        if 'learning_goals' not in table_names:
            sync_conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS learning_goals (
                    id VARCHAR NOT NULL PRIMARY KEY,
                    user_id VARCHAR NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    name VARCHAR(200) NOT NULL,
                    type VARCHAR(20) NOT NULL DEFAULT 'weekly',
                    scope_notes JSON,
                    scope_folders JSON,
                    target_mastery FLOAT NOT NULL DEFAULT 80.0,
                    deadline DATETIME,
                    status VARCHAR(20) NOT NULL DEFAULT 'active',
                    progress_cache FLOAT DEFAULT 0.0 NOT NULL,
                    last_progress_refresh DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
                )
                """
            ))
            sync_conn.execute(text(
                "CREATE INDEX ix_learning_goals_user_id ON learning_goals (user_id)"
            ))
            sync_conn.execute(text(
                "CREATE INDEX ix_learning_goals_status ON learning_goals (status)"
            ))
            logger.info("SQLite 迁移: 已创建 learning_goals 表")

        # 检查并创建 daily_plans 表（防御性建表，对应 Alembic 004 迁移）
        if 'daily_plans' not in table_names:
            sync_conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS daily_plans (
                    id VARCHAR NOT NULL PRIMARY KEY,
                    goal_id VARCHAR NOT NULL REFERENCES learning_goals(id) ON DELETE CASCADE,
                    user_id VARCHAR NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    plan_date DATETIME NOT NULL,
                    recommended_tasks JSON,
                    completed_count INTEGER DEFAULT 0 NOT NULL,
                    total_count INTEGER DEFAULT 0 NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
                )
                """
            ))
            sync_conn.execute(text(
                "CREATE INDEX ix_daily_plans_user_id ON daily_plans (user_id)"
            ))
            sync_conn.execute(text(
                "CREATE INDEX ix_daily_plans_goal_id ON daily_plans (goal_id)"
            ))
            sync_conn.execute(text(
                "CREATE INDEX ix_daily_plans_plan_date ON daily_plans (plan_date)"
            ))
            logger.info("SQLite 迁移: 已创建 daily_plans 表")

        # 检查 assessment_results 表是否缺少 link_signature / is_stale 列
        if 'assessment_results' in table_names:
            existing_columns = {col['name'] for col in inspector.get_columns('assessment_results')}
            if 'link_signature' not in existing_columns:
                sync_conn.execute(text(
                    "ALTER TABLE assessment_results ADD COLUMN link_signature VARCHAR(64)"
                ))
                logger.info("SQLite 迁移: 已为 assessment_results 表添加 link_signature 列")
            if 'is_stale' not in existing_columns:
                sync_conn.execute(text(
                    "ALTER TABLE assessment_results ADD COLUMN is_stale BOOLEAN DEFAULT 0 NOT NULL"
                ))
                logger.info("SQLite 迁移: 已为 assessment_results 表添加 is_stale 列")

        # 检查 knowledge_cards 表是否缺少新增的 6 列
        # （card_category / is_key_point / is_difficulty / mastery_level / source_note_ids / parent_card_id）
        # 对应 Alembic 003 迁移；create_all 不会 ALTER 已有表，需在此补齐
        if 'knowledge_cards' in table_names:
            existing_columns = {col['name'] for col in inspector.get_columns('knowledge_cards')}
            if 'card_category' not in existing_columns:
                sync_conn.execute(text(
                    "ALTER TABLE knowledge_cards ADD COLUMN card_category VARCHAR DEFAULT 'regular' NOT NULL"
                ))
                logger.info("SQLite 迁移: 已为 knowledge_cards 表添加 card_category 列")
            if 'is_key_point' not in existing_columns:
                sync_conn.execute(text(
                    "ALTER TABLE knowledge_cards ADD COLUMN is_key_point BOOLEAN DEFAULT 0 NOT NULL"
                ))
                logger.info("SQLite 迁移: 已为 knowledge_cards 表添加 is_key_point 列")
            if 'is_difficulty' not in existing_columns:
                sync_conn.execute(text(
                    "ALTER TABLE knowledge_cards ADD COLUMN is_difficulty BOOLEAN DEFAULT 0 NOT NULL"
                ))
                logger.info("SQLite 迁移: 已为 knowledge_cards 表添加 is_difficulty 列")
            if 'mastery_level' not in existing_columns:
                sync_conn.execute(text(
                    "ALTER TABLE knowledge_cards ADD COLUMN mastery_level FLOAT DEFAULT 0 NOT NULL"
                ))
                logger.info("SQLite 迁移: 已为 knowledge_cards 表添加 mastery_level 列")
            if 'source_note_ids' not in existing_columns:
                sync_conn.execute(text(
                    "ALTER TABLE knowledge_cards ADD COLUMN source_note_ids JSON"
                ))
                logger.info("SQLite 迁移: 已为 knowledge_cards 表添加 source_note_ids 列")
            if 'parent_card_id' not in existing_columns:
                sync_conn.execute(text(
                    "ALTER TABLE knowledge_cards ADD COLUMN parent_card_id VARCHAR"
                ))
                logger.info("SQLite 迁移: 已为 knowledge_cards 表添加 parent_card_id 列")
                # 为 parent_card_id 创建索引以加速拓展卡片查询
                try:
                    sync_conn.execute(text(
                        "CREATE INDEX ix_knowledge_cards_parent_card_id ON knowledge_cards (parent_card_id)"
                    ))
                    logger.info("SQLite 迁移: 已为 knowledge_cards.parent_card_id 创建索引")
                except Exception:
                    # 索引已存在时忽略
                    pass

        # 邮件提醒用户级开关与去重记录（对应 Alembic 010）
        if 'users' in table_names:
            existing_columns = {col['name'] for col in inspector.get_columns('users')}
            if 'email_reminder_enabled' not in existing_columns:
                sync_conn.execute(text(
                    "ALTER TABLE users ADD COLUMN email_reminder_enabled BOOLEAN DEFAULT 1 NOT NULL"
                ))
                logger.info("SQLite 迁移: 已为 users 表添加 email_reminder_enabled 列")
            if 'last_reminded_at' not in existing_columns:
                sync_conn.execute(text(
                    "ALTER TABLE users ADD COLUMN last_reminded_at DATETIME"
                ))
                logger.info("SQLite 迁移: 已为 users 表添加 last_reminded_at 列")

        # 复习记录的用户自评列（四档自评闭环）
        #
        # 这是一条**纯加列**迁移：nullable、无默认值、不触碰任何已有行，
        # 因此不经过 ENGRAMNOTE_ALLOW_DESTRUCTIVE_MIGRATION 闸门。
        # 语义上它把 review_logs 从「只有一个质量分」升级为
        # 「自动判分分 + 用户自评分」双信号，是校准曲线的前置条件。
        if 'review_logs' in table_names:
            existing_columns = {col['name'] for col in inspector.get_columns('review_logs')}
            if 'self_rating' not in existing_columns:
                sync_conn.execute(text(
                    "ALTER TABLE review_logs ADD COLUMN self_rating INTEGER"
                ))
                logger.info("SQLite 迁移: 已为 review_logs 表添加 self_rating 列")
            if 'grading_method' not in existing_columns:
                # 历史行回填 'legacy'：它们的判分方式已不可考，
                # 不能用 'ungraded' 冒充（那会污染「自动 vs 自评」不一致率的分母）。
                sync_conn.execute(text(
                    "ALTER TABLE review_logs ADD COLUMN grading_method VARCHAR(16) "
                    "NOT NULL DEFAULT 'legacy'"
                ))
            if 'grading_detail' not in existing_columns:
                # 阶段 3.5：LLM 语义判分的结构化明细（verdict/缺失点/误解点/置信度）。
                # 纯加列、nullable、不触碰已有行。历史行保持 NULL ——
                # 那时没有语义判分，NULL 是**如实**的表达，不用空对象冒充。
                sync_conn.execute(text(
                    "ALTER TABLE review_logs ADD COLUMN grading_detail JSON"
                ))
                logger.info("SQLite 迁移: 已为 review_logs 表添加 grading_detail 列")
                logger.info("SQLite 迁移: 已为 review_logs 表添加 grading_method 列（历史行回填 legacy）")
            if 'card_id' not in existing_columns:
                # 阶段 3.2：把复习记录归到**卡片**上。
                #
                # 为什么必须显式列出：`create_all()` 只建缺失的表，
                # 不会给已有表加列；`_migrate_sqlite` 也只加这里显式写出的列。
                # 也就是说**新增一个模型字段时，必须同时在这里登记** ——
                # 否则真库永远缺这一列，而全新库却有（本轮实测踩到，
                # 表现为 `no such column: card_id`，且只在真库复现）。
                sync_conn.execute(text(
                    "ALTER TABLE review_logs ADD COLUMN card_id VARCHAR REFERENCES knowledge_cards(id)"
                ))
                logger.info("SQLite 迁移: 已为 review_logs 表添加 card_id 列")
                try:
                    sync_conn.execute(text(
                        "CREATE INDEX IF NOT EXISTS ix_review_logs_card_id ON review_logs (card_id)"
                    ))
                except Exception:
                    pass
                # 回填：从 quiz_id 反查所属卡片，让历史记录也能被归到卡片上
                # （度量层按 card_id 聚合，不回填的话老数据参与不了保持率配对）
                try:
                    result = sync_conn.execute(text(
                        """
                        UPDATE review_logs
                           SET card_id = (
                               SELECT qi.card_id FROM quiz_items qi
                                WHERE qi.id = review_logs.quiz_id
                           )
                         WHERE card_id IS NULL AND quiz_id IS NOT NULL
                        """
                    ))
                    if result.rowcount:
                        logger.info("SQLite 迁移: 已回填 %d 条 review_logs.card_id", result.rowcount)
                except Exception as exc:
                    logger.warning("回填 review_logs.card_id 失败（不影响启动）: %s", exc)

            # ---- 阶段 3.2 / 3.6：复习记录的事件流化字段 ----
            #
            # 三列都是纯加列、nullable、不触碰已有行，因此不经过
            # ENGRAMNOTE_ALLOW_DESTRUCTIVE_MIGRATION 闸门。
            #
            # ⚠️ `predicted_retention` 是**单向门**：它是"复习前调度器预测的
            # 可回忆概率"，只能在复习那一刻写下。历史行永远是 NULL ——
            # 那时没有 FSRS，而用 SM-2 的近似公式补算会往校准曲线的分母里
            # 混入不是"预测"的数字。宁缺勿滥。
            if 'rating' not in existing_columns:
                sync_conn.execute(text(
                    "ALTER TABLE review_logs ADD COLUMN rating INTEGER"
                ))
                logger.info("SQLite 迁移: 已为 review_logs 表添加 rating 列")
            if 'predicted_retention' not in existing_columns:
                sync_conn.execute(text(
                    "ALTER TABLE review_logs ADD COLUMN predicted_retention REAL"
                ))
                logger.info("SQLite 迁移: 已为 review_logs 表添加 predicted_retention 列")
            if 'item_type' not in existing_columns:
                sync_conn.execute(text(
                    "ALTER TABLE review_logs ADD COLUMN item_type VARCHAR(16)"
                ))
                try:
                    sync_conn.execute(text(
                        "CREATE INDEX IF NOT EXISTS ix_review_logs_item_type "
                        "ON review_logs (item_type)"
                    ))
                except Exception:
                    pass
                # 回填：本列引入前的行，用 quiz_id 是否存在区分卡片级复习与答题。
                # 这是**如实**的判断（那一列本来就是这么用的），不是猜测；
                # 判不出来（quiz_id 与 card_id 都为空）的行保持 NULL。
                try:
                    result = sync_conn.execute(text(
                        """
                        UPDATE review_logs
                           SET item_type = CASE
                                   WHEN quiz_id IS NOT NULL THEN 'quiz'
                                   WHEN card_id IS NOT NULL THEN 'card'
                                   ELSE NULL
                               END
                         WHERE item_type IS NULL
                        """
                    ))
                    if result.rowcount:
                        logger.info(
                            "SQLite 迁移: 已回填 %d 条 review_logs.item_type", result.rowcount
                        )
                except Exception as exc:
                    logger.warning("回填 review_logs.item_type 失败（不影响启动）: %s", exc)

        # ---- review_states 表迁移（阶段 3.6：FSRS 记忆状态）----
        if 'review_states' in table_names:
            state_cols = {c['name'] for c in inspector.get_columns('review_states')}
            if 'stability' not in state_cols:
                # nullable 且**不填默认值**：真库里 2241 行 SM-2 时期的状态
                # 并不知道自己的 S/D。填 0 或 1 会让"没算过"与"算出来就这么小"
                # 再也分不开，而两者的处置不同（前者要由 adopt_legacy_state
                # 从 interval/EF 换算接管，后者直接用）。
                sync_conn.execute(text(
                    "ALTER TABLE review_states ADD COLUMN stability REAL"
                ))
                logger.info("SQLite 迁移: 已为 review_states 表添加 stability 列")
            if 'difficulty' not in state_cols:
                sync_conn.execute(text(
                    "ALTER TABLE review_states ADD COLUMN difficulty REAL"
                ))
                logger.info("SQLite 迁移: 已为 review_states 表添加 difficulty 列")

        # ---- knowledge_cards 表迁移（阶段 4.8：重跑理解的内容寻址键）----
        if 'knowledge_cards' in table_names:
            card_cols = {c['name'] for c in inspector.get_columns('knowledge_cards')}
            if 'content_hash' not in card_cols:
                sync_conn.execute(text(
                    "ALTER TABLE knowledge_cards ADD COLUMN content_hash VARCHAR(64)"
                ))
                try:
                    sync_conn.execute(text(
                        "CREATE INDEX IF NOT EXISTS ix_knowledge_cards_content_hash "
                        "ON knowledge_cards (content_hash)"
                    ))
                except Exception:
                    pass
                # 回填历史行：**复用 `card_intake_service.card_content_hash`**，
                # 不在这里另写一套规范化。
                #
                # 为什么必须共用：若两边规则有差异（哪怕只差一个 NFKC 归一），
                # 历史卡片的指纹就会与"同样内容的新卡"算出的指纹不同 ——
                # 于是重跑理解时每张历史卡都看起来"从没出现过"，全被重新插入，
                # 恰好造成这次要修的那种重复。**这里图省事抄一份规则，
                # 等于把幂等性悄悄关掉。**
                try:
                    from .services.card_intake_service import card_content_hash

                    rows = sync_conn.execute(text(
                        "SELECT id, title, content FROM knowledge_cards "
                        "WHERE content_hash IS NULL"
                    )).fetchall()
                    for card_id, title, content in rows:
                        sync_conn.execute(
                            text("UPDATE knowledge_cards SET content_hash = :h WHERE id = :i"),
                            {"h": card_content_hash(title, content), "i": card_id},
                        )
                    if rows:
                        logger.info(
                            "SQLite 迁移: 已回填 %d 行 knowledge_cards.content_hash", len(rows)
                        )
                except Exception as exc:
                    logger.warning(
                        "回填 knowledge_cards.content_hash 失败（不影响启动；"
                        "未回填的行不参与去重判定，重跑理解时会多插一张卡）: %s", exc,
                    )

        # ---- llm_calls 表迁移（阶段 4.7：响应缓存的记账字段）----
        #
        # ⚠️ `llm_cache` 是**新表**，`create_all` 会建；但 `llm_calls` 在
        # 阶段 4.2 就已经存在了，`create_all` **不会**给它加列 ——
        # 这正是本文件反复强调的那个陷阱（"新增一个模型字段时必须同时在这里
        # 登记，否则真库永远缺这一列，而全新库却有"）。
        if 'llm_calls' in table_names:
            call_cols = {c['name'] for c in inspector.get_columns('llm_calls')}
            if 'cached' not in call_cols:
                # NOT NULL + DEFAULT 0：历史行都是"没命中缓存"，语义明确，
                # 且不需要回填（DEFAULT 会填好）。
                sync_conn.execute(text(
                    "ALTER TABLE llm_calls ADD COLUMN cached BOOLEAN NOT NULL DEFAULT 0"
                ))
                logger.info("SQLite 迁移: 已为 llm_calls 表添加 cached 列")
            if 'saved_tokens' not in call_cols:
                # nullable：未命中时是"没有节省量"（NULL），不是 0
                sync_conn.execute(text(
                    "ALTER TABLE llm_calls ADD COLUMN saved_tokens INTEGER"
                ))
                logger.info("SQLite 迁移: 已为 llm_calls 表添加 saved_tokens 列")

        # ---- chunks 表迁移（阶段 2.2′ / 2.5′）----
        if 'chunks' in table_names:
            chunk_cols = {c['name'] for c in inspector.get_columns('chunks')}
            if 'grams' not in chunk_cols:
                # 同上：新增模型字段必须在此登记，否则真库永远缺这一列
                sync_conn.execute(text("ALTER TABLE chunks ADD COLUMN grams TEXT"))
                logger.info("SQLite 迁移: 已为 chunks 表添加 grams 列")
            if 'chunk_rowid' not in chunk_cols:
                # 整数 rowid：FTS5 外部内容表的 content_rowid 必须指向它。
                # VARCHAR 主键会被 SQLite 当成 rowid 的别名，导致 JOIN 对不上
                # 而静默返回 0 条（见 models/chunk.py 的说明）。
                sync_conn.execute(text(
                    "ALTER TABLE chunks ADD COLUMN chunk_rowid INTEGER"
                ))
                sync_conn.execute(text(
                    "UPDATE chunks SET chunk_rowid = ("
                    "  SELECT COUNT(*) FROM chunks c2 "
                    "   WHERE c2.note_id < chunks.note_id "
                    "      OR (c2.note_id = chunks.note_id AND c2.[index] <= chunks.[index])"
                    ")"
                ))
                sync_conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_chunks_chunk_rowid "
                    "ON chunks (chunk_rowid)"
                ))
                logger.info("SQLite 迁移: 已为 chunks 表添加 chunk_rowid 列并回填")
            # 回填 bigram 切词结果（FTS5 索引读取这一列）
            #
            # 幂等：只处理 grams 为空的行。切词在 Python 侧完成 ——
            # SQL 里做相邻字符滑窗既不可读也易错，而这里是**一次性回填**，
            # 不在热路径上（chunks 只在清洗完成时重建）。
            try:
                pending = sync_conn.execute(text(
                    "SELECT rowid, content FROM chunks "
                    "WHERE grams IS NULL OR grams = '' LIMIT 5000"
                )).fetchall()
                if pending:
                    sync_conn.execute(
                        text("UPDATE chunks SET grams = :g WHERE rowid = :r"),
                        [
                            {"g": _bigrams_for_migration(row[1] or ""), "r": row[0]}
                            for row in pending
                        ],
                    )
                    logger.info("SQLite 迁移: 已回填 %d 条 chunks.grams", len(pending))
            except Exception as exc:
                logger.warning("回填 chunks.grams 失败（不影响启动）: %s", exc)

        # ---- 孤儿数据检查（只报告，不删除）----
        # 历史上此处会无条件 DELETE 孤儿行。两个问题：
        # 1) 它在**每个进程启动时**执行，是一条不可回退的数据删除；
        # 2) review_logs 是用户最不可再生的学习记录（唯一的记忆强度证据），
        #    把「quiz_id 指向的题目已不存在」的历史记录当作垃圾删除，
        #    等于在用户重新生成题目后销毁他的复习历史。
        # 现改为**只统计并告警**。确需清理时由运维显式运行 scripts/ 下的脚本。
        # 所有语句的表名/列名均为代码内硬编码白名单，整条 SQL 写死。
        # (查询语句, 目标表, 孤儿字段, 父表名)
        orphan_checks = [
            ("SELECT COUNT(*) FROM review_logs WHERE quiz_id IS NOT NULL AND quiz_id NOT IN (SELECT id FROM quiz_items)",
             "review_logs", "quiz_id", "quiz_items"),
            ("SELECT COUNT(*) FROM review_logs WHERE note_id IS NOT NULL AND note_id NOT IN (SELECT id FROM notes)",
             "review_logs", "note_id", "notes"),
            ("SELECT COUNT(*) FROM quiz_items WHERE card_id IS NOT NULL AND card_id NOT IN (SELECT id FROM knowledge_cards)",
             "quiz_items", "card_id", "knowledge_cards"),
            ("SELECT COUNT(*) FROM quiz_items WHERE note_id IS NOT NULL AND note_id NOT IN (SELECT id FROM notes)",
             "quiz_items", "note_id", "notes"),
            ("SELECT COUNT(*) FROM knowledge_cards WHERE note_id IS NOT NULL AND note_id NOT IN (SELECT id FROM notes)",
             "knowledge_cards", "note_id", "notes"),
            ("SELECT COUNT(*) FROM card_relations WHERE card_id_1 IS NOT NULL AND card_id_1 NOT IN (SELECT id FROM knowledge_cards)",
             "card_relations", "card_id_1", "knowledge_cards"),
            ("SELECT COUNT(*) FROM card_relations WHERE card_id_2 IS NOT NULL AND card_id_2 NOT IN (SELECT id FROM knowledge_cards)",
             "card_relations", "card_id_2", "knowledge_cards"),
        ]

        for sql, table, col, parent_table in orphan_checks:
            if table not in table_names or parent_table not in table_names:
                continue
            count = sync_conn.execute(text(sql)).scalar() or 0
            if count > 0:
                logger.warning(
                    "孤儿数据检查: %s 表有 %d 条 %s 指向不存在的 %s（未删除，仅报告）",
                    table, count, col, parent_table,
                )

        # note_projects 标签关联表：清理指向不存在笔记或项目的孤儿行
        # （纯关联表，删除孤儿行无信息损失，且不影响学习记录，保持原行为）
        if 'note_projects' in table_names:
            result = sync_conn.execute(text(
                "DELETE FROM note_projects WHERE note_id NOT IN (SELECT id FROM notes) "
                "OR project_id NOT IN (SELECT id FROM projects)"
            ))
            if result.rowcount > 0:
                logger.info(f"孤儿数据清理: 从 note_projects 中清理了 {result.rowcount} 条标签孤儿记录")

        # ---- card_relations：唯一索引 + 可选去重（见 §2.6 M-3） ----
        # 键必须与去重口径严格一致，否则会出现「去重认为不同组、建索引时却冲突」
        # 的经典错配：旧代码 GROUP BY 含 status，而唯一索引只有 4 列（漏 status），
        # 于是存在同对卡片不同 status 的行时索引创建必然抛 UNIQUE 失败，
        # 且该异常被下面的 except 吞成一条 warning —— 致使这条唯一约束
        # 在生产**可能压根不存在**，而所有依赖它的去重防护（F-01/F-17）形同虚设。
        #
        # 现统一为 5 列口径：(user_id, card_id_1, card_id_2, relation_type, status)。
        # 去重是破坏性删除，改为仅在显式放行时执行；索引创建是无损的，始终执行。
        if 'card_relations' in table_names:
            if _destructive_migration_allowed():
                logger.warning("检测到 ENGRAMNOTE_ALLOW_DESTRUCTIVE_MIGRATION，执行 card_relations 同键去重")
                result = sync_conn.execute(text(
                    """
                    DELETE FROM card_relations WHERE id NOT IN (
                        SELECT MIN(id) FROM card_relations
                        GROUP BY user_id, card_id_1, card_id_2, relation_type, status
                    )
                    """
                ))
                if result.rowcount > 0:
                    logger.warning("card_relations 同键去重: 删除 %d 行", result.rowcount)
            else:
                dup = sync_conn.execute(text(
                    """
                    SELECT COUNT(*) FROM (
                        SELECT 1 FROM card_relations
                        GROUP BY user_id, card_id_1, card_id_2, relation_type, status
                        HAVING COUNT(*) > 1
                    )
                    """
                )).scalar() or 0
                if dup:
                    logger.warning(
                        "card_relations 存在 %d 组同键重复（未删除；如需清理请设置 "
                        "ENGRAMNOTE_ALLOW_DESTRUCTIVE_MIGRATION=1 后重启）", dup
                    )

            # 幂等建唯一索引（5 列，与上面的 GROUP BY 口径一致）
            try:
                sync_conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_card_relations_pair_type "
                    "ON card_relations (user_id, card_id_1, card_id_2, relation_type, status)"
                ))
                logger.info("SQLite 迁移: 已确保 card_relations 唯一索引 uq_card_relations_pair_type 存在")
            except Exception as e:
                # 不静默：索引缺失会让重复关系防护失效，必须显式告警
                logger.error(
                    "创建 card_relations 唯一索引失败（重复关系防护未生效，请人工处理）: %s", e
                )

    await conn.run_sync(_do_migrate)


def _acquire_schema_lock(lock_handle) -> None:
    """
    获取跨平台文件锁（阻塞直到获得）

    POSIX 使用 fcntl.flock(LOCK_EX)，Windows 使用 msvcrt.locking。
    用于序列化多实例（API + Celery worker/beat）并发触碰 SQLite schema，
    避免同时重建悬挂引用表导致的崩溃或数据丢失。
    """
    import os

    if os.name == "nt":
        import msvcrt
        import time

        # msvcrt.locking 要求锁定区域存在，先确保文件至少 1 字节
        lock_handle.seek(0, os.SEEK_END)
        if lock_handle.tell() == 0:
            lock_handle.write(b"\0")
            lock_handle.flush()
        # LK_NBLCK 非阻塞尝试 + 短等待，模拟 fcntl 的阻塞语义
        while True:
            lock_handle.seek(0)
            try:
                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                time.sleep(0.1)
    else:
        import fcntl

        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)


def _release_schema_lock(lock_handle) -> None:
    """释放 _acquire_schema_lock 获取的跨平台文件锁"""
    import os

    if os.name == "nt":
        import msvcrt

        lock_handle.seek(0)
        msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


async def _rebuild_dangling_tables() -> None:
    """
    回收站悬挂引用改造：把 5 张表的外键端改为可空 + ON DELETE SET NULL

    涉及表：card_relations（card_id_1/2）、note_material_links（两端）、
    knowledge_cards（note_id）、quiz_items（note_id）、review_logs（note_id）。

    SQLite 不支持 ALTER COLUMN / 修改外键，必须"建新表 → 拷数据 → 改名"重建；
    而 PRAGMA foreign_keys 是连接级开关且不能在事务内切换，故此函数用独立的
    raw sqlite3 连接执行。

    ⚠️ **已从启动路径移除**（见 init_db 的说明）：本函数会 DROP 业务表，
    原先在 API / worker / beat 三个进程启动时都会执行，任意中断都可能丢数据。
    现在只有在显式设置 ENGRAMNOTE_ALLOW_DESTRUCTIVE_MIGRATION=1 时才执行，
    供一次性升级使用；未设置时仅**探测并告警**，不做任何修改。

    幂等：通过 PRAGMA table_info 检测探测列 notnull，已符合新 schema 则跳过。
    内存库（测试场景）无法跨连接访问，直接跳过——内存库必为 create_all 新建，天然新 schema。
    """
    import sqlite3
    from urllib.parse import unquote, urlparse

    from sqlalchemy.dialects import sqlite as sqlite_dialect
    from sqlalchemy.schema import CreateIndex, CreateTable

    # `Base.metadata` 只包含**已被导入**的模型的表。本模块从不导入
    # `app.models`，因此在独立脚本里调用本函数时 metadata 可能是空的，
    # 表现为 `KeyError: 'review_logs'`（本轮实测踩到，且异常发生在
    # 事务开始前、DROP 之前，所以没有造成数据损失）。
    from . import models  # noqa: F401 — 副作用导入，注册全部表定义

    allowed = _destructive_migration_allowed()

    # 解析 SQLite 文件路径（sqlite+aiosqlite:///path）
    # 使用 settings.get_database_url() 而非模块级 database_url，以适配
    # 运行时（如测试）重建 settings 后数据库路径发生变化的场景。
    db_url = settings.get_database_url()
    db_file = unquote(urlparse(db_url.replace("+aiosqlite", "")).path or "")
    if not db_file or db_file == ":memory:":
        return
    # Windows 下 urlparse 可能把盘符放进 netloc（//d:/x.db 形式）
    if not db_file.startswith("/"):
        netloc = urlparse(db_url.replace("+aiosqlite", "")).netloc
        if netloc:
            db_file = netloc + urlparse(db_url.replace("+aiosqlite", "")).path

    # (表名, 探测列)：探测列 notnull=1 视为旧 schema 需重建
    #
    # `review_logs.quiz_id` 是阶段 3.12（卡片可直接复习）引入的变更：
    # 卡片级复习没有题目，因此该列必须可空。
    # SQLite 不能直接 `ALTER COLUMN ... DROP NOT NULL`，只能重建表 ——
    # 正好复用本函数已有的"按 metadata 生成 DDL → 拷数据 → 换名"机制，
    # 它天然会让重建后的表带上新的 nullable 约束。
    targets = (
        ("card_relations", "card_id_1"),
        ("note_material_links", "personal_note_id"),
        ("knowledge_cards", "note_id"),
        ("quiz_items", "note_id"),
        ("review_logs", "note_id"),
        ("review_logs", "quiz_id"),
    )

    # 未放行时：只探测旧 schema 并告警，绝不修改任何数据
    if not allowed:
        try:
            probe = sqlite3.connect(db_file)
            try:
                stale = []
                for table, probe_col in targets:
                    for row in probe.execute(f"PRAGMA table_info({table})"):
                        if row[1] == probe_col and bool(row[3]):
                            stale.append(table)
                            break
            finally:
                probe.close()
            if stale:
                logger.error(
                    "检测到旧 schema 表需要重建（%s），但破坏性迁移未放行 —— 本次启动不做任何修改。"
                    "如需升级：先备份数据库，再设置 ENGRAMNOTE_ALLOW_DESTRUCTIVE_MIGRATION=1 重启一次。",
                    stale,
                )
        except Exception as e:
            logger.warning("旧 schema 探测失败（忽略）: %s", e)
        return

    logger.warning("检测到 ENGRAMNOTE_ALLOW_DESTRUCTIVE_MIGRATION，执行破坏性表重建")

    # 跨进程文件锁：API 与 Celery worker/beat 可能同时启动并各自执行 init_db，
    # 并发重建悬挂引用表会互相 DROP/ALTER 导致数据丢失或崩溃，故以
    # data/db/.schema-lock 串行化重建；锁内重放探测（双检）确保等待期间
    # 他人已完成重建时不再重复重建。
    from .config import DB_DIR

    lock_path = DB_DIR / ".schema-lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with open(lock_path, "a+b") as lock_handle:
        _acquire_schema_lock(lock_handle)
        try:
            raw = sqlite3.connect(db_file)
            try:
                raw.execute("PRAGMA foreign_keys=OFF")

                def _needs_rebuild(table: str, probe_col: str) -> bool:
                    for row in raw.execute(f"PRAGMA table_info({table})"):
                        if row[1] == probe_col:
                            return bool(row[3])  # notnull 标志
                    return False

                # 双检：锁内重放探测，避免等待锁期间已被其他实例重建而重复执行
                #
                # 必须**去重**：同一张表可能因为多个探测列未达标而出现多次
                # （例如 review_logs 的 note_id 与 quiz_id 都不满足）。
                # 不去重会对同一张表连续重建两次，第二次必然在
                # `CREATE TABLE ...temp` 或 `DROP TABLE` 处报错。
                todo = list(dict.fromkeys(
                    t for t, probe in targets if _needs_rebuild(t, probe)
                ))
                if not todo:
                    return

                logger.info("SQLite 迁移: 检测到旧 schema，开始重建悬挂引用表: %s", todo)
                missing = [t for t in todo if t not in Base.metadata.tables]
                if missing:
                    raise RuntimeError(
                        f"metadata 中缺少表定义 {missing} —— 请在导入 app.models 后调用本函数"
                    )
                raw.execute("BEGIN")
                try:
                    for table_name in todo:
                        table = Base.metadata.tables[table_name]
                        temp_name = f"{table_name}__trash_rebuild"

                        # 用 SQLAlchemy 按 metadata 生成与 create_all 完全一致的 DDL
                        create_ddl = str(
                            CreateTable(table).compile(dialect=sqlite_dialect.dialect())
                        ).strip()
                        create_ddl = create_ddl.replace(
                            f"CREATE TABLE {table_name}", f'CREATE TABLE "{temp_name}"', 1
                        )

                        col_list = ", ".join(f'"{c.name}"' for c in table.columns)
                        raw.execute(f'DROP TABLE IF EXISTS "{temp_name}"')
                        raw.execute(create_ddl)
                        raw.execute(
                            f'INSERT INTO "{temp_name}" ({col_list}) '
                            f'SELECT {col_list} FROM "{table_name}"'
                        )
                        raw.execute(f'DROP TABLE "{table_name}"')
                        raw.execute(f'ALTER TABLE "{temp_name}" RENAME TO "{table_name}"')
                        # 旧索引随 DROP TABLE 消失，按 metadata 重建
                        for idx in table.indexes:
                            raw.execute(str(CreateIndex(idx).compile(dialect=sqlite_dialect.dialect())))
                    raw.execute("COMMIT")
                except Exception:
                    raw.execute("ROLLBACK")
                    raise

                # 一致性校验：FK 违规仅告警（重建按原数据原样拷贝，理论上不会出现）
                violations = raw.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    logger.warning("悬挂引用表重建后 FK 校验异常（保留数据，仅告警）: %s", violations[:5])
                logger.info("SQLite 迁移: 悬挂引用表重建完成")
            finally:
                try:
                    raw.execute("PRAGMA foreign_keys=ON")
                finally:
                    raw.close()
        finally:
            _release_schema_lock(lock_handle)


if __name__ == "__main__":
    pass
