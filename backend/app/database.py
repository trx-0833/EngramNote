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

import logging

from .config import get_settings

# 获取全局配置
settings = get_settings()
logger = logging.getLogger(__name__)

# 提示当前模式
if settings.debug:
    logger.warning("当前处于 DEBUG 模式")

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

    注意：create_all() 只创建不存在的表，不会对已有表做 ALTER TABLE。
    因此在 SQLite 开发模式下，额外执行简易迁移以补充缺失的列。
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # 对 SQLite：检查并补充已有表缺失的列
        if _is_sqlite:
            await _migrate_sqlite(conn)

    # 清理两阶段上传遗留的超时临时目录（与 DB 后端无关，启动时兜底执行）
    cleanup_stale_uploads()


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
        if 'note_material_links' not in table_names:
            sync_conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS note_material_links (
                    id VARCHAR NOT NULL PRIMARY KEY,
                    user_id VARCHAR NOT NULL REFERENCES users(id),
                    personal_note_id VARCHAR NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                    material_note_id VARCHAR NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
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

        # ---- 孤儿数据清理 ----
        # 在级联删除修复之前，删除笔记/卡片不会级联删除关联数据，
        # 加上 SQLite 默认不启用外键约束，可能存在指向已删除父记录的孤儿数据。
        # 按外键依赖顺序从叶子到根清理，避免清理顺序导致二次孤儿。
        orphan_checks = [
            # (表名, 孤儿字段, 父表名, 父字段, 操作类型: delete 或 nullify)
            ("review_logs", "quiz_id", "quiz_items", "id", "delete"),
            ("review_logs", "note_id", "notes", "id", "delete"),
            ("quiz_items", "card_id", "knowledge_cards", "id", "delete"),
            ("quiz_items", "note_id", "notes", "id", "delete"),
            ("knowledge_cards", "note_id", "notes", "id", "delete"),
            ("card_relations", "card_id_1", "knowledge_cards", "id", "delete"),
            ("card_relations", "card_id_2", "knowledge_cards", "id", "delete"),
            ("notes", "folder_id", "folders", "id", "nullify"),
        ]

        for table, col, parent_table, parent_col, action in orphan_checks:
            if table not in table_names or parent_table not in table_names:
                continue
            if action == "delete":
                result = sync_conn.execute(text(
                    f"DELETE FROM {table} WHERE {col} IS NOT NULL AND {col} NOT IN (SELECT {parent_col} FROM {parent_table})"
                ))
            elif action == "nullify":
                result = sync_conn.execute(text(
                    f"UPDATE {table} SET {col} = NULL WHERE {col} IS NOT NULL AND {col} NOT IN (SELECT {parent_col} FROM {parent_table})"
                ))
            if result.rowcount > 0:
                logger.info(f"孤儿数据清理: 从 {table} 中清理了 {result.rowcount} 条 {col} 孤儿记录")

        # note_projects 标签关联表：清理指向不存在笔记或项目的孤儿行
        # notes.project_id 单值外键已移除，标签关系全部承载于此表
        if 'note_projects' in table_names:
            result = sync_conn.execute(text(
                "DELETE FROM note_projects WHERE note_id NOT IN (SELECT id FROM notes) "
                "OR project_id NOT IN (SELECT id FROM projects)"
            ))
            if result.rowcount > 0:
                logger.info(f"孤儿数据清理: 从 note_projects 中清理了 {result.rowcount} 条标签孤儿记录")

        # ---- 重复关系清理 ----
        # 同一对卡片（无向，忽略方向）不应存在多条关系（历史版本可能因缺少去重产生重复，
        # 导致节点关联数被重复计算）。按 (user_id, 小id, 大id) 分组，每组仅保留最小 id 的一条。
        if 'card_relations' in table_names:
            result = sync_conn.execute(text(
                """
                DELETE FROM card_relations WHERE id NOT IN (
                    SELECT MIN(id) FROM card_relations
                    GROUP BY user_id,
                             CASE WHEN card_id_1 < card_id_2 THEN card_id_1 ELSE card_id_2 END,
                             CASE WHEN card_id_1 < card_id_2 THEN card_id_2 ELSE card_id_1 END
                )
                """
            ))
            if result.rowcount > 0:
                logger.info(f"重复关系清理: 清理了 {result.rowcount} 条重复卡片关系")

    await conn.run_sync(_do_migrate)


if __name__ == "__main__":
    pass
