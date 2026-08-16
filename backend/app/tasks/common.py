"""
Celery 任务公共模块（F-27 修复）

收敛 4 个任务模块中重复的：
- 独立数据库引擎/会话工厂（convert/clean/understand/embedding 各一份 → 此处一份）
- 笔记状态更新 `_update_note_status`（三份 → 此处一份，统一白名单 + metadata merge）
- 笔记状态查询 `_get_note_status`

注意：Celery worker 运行在独立进程中，需要自己的数据库连接；
模块级单例引擎在整个 worker 生命周期复用。
"""

import logging
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..models.note import Note, NoteStatus
from ..services.vault_meta import write_note_meta

logger = logging.getLogger(__name__)
settings = get_settings()

# update_note_status 允许更新的字段白名单（防任意字段写入，旧 C-3 修复）
_UPDATABLE_NOTE_FIELDS = {"title", "page_count", "original_md_path", "clean_md_path", "metadata_"}

# 进程级单例引擎/会话工厂
_sync_engine = None
_sync_session_factory: Optional[async_sessionmaker] = None


def get_sync_session() -> async_sessionmaker:
    """
    获取 Celery worker 进程级共享的数据库会话工厂

    仅首次调用时创建引擎；与 database.py 主引擎一样注册 SQLite
    PRAGMA（外键 + busy_timeout），保证 worker 与 API 行为一致。
    """
    global _sync_engine, _sync_session_factory
    if _sync_session_factory is None:
        _sync_engine = create_async_engine(settings.get_database_url(), echo=False)
        if settings.get_database_url().startswith("sqlite"):
            from ..database import register_sqlite_pragmas
            register_sqlite_pragmas(_sync_engine)
        _sync_session_factory = async_sessionmaker(_sync_engine, expire_on_commit=False)
    return _sync_session_factory


async def get_note_status(note_id: str) -> Optional[NoteStatus]:
    """查询笔记当前状态（用于停止/删除检查）"""
    session_factory = get_sync_session()
    async with session_factory() as session:
        result = await session.execute(select(Note).where(Note.id == note_id))
        note = result.scalars().first()
        return note.status if note else None


async def update_note_status(
    note_id: str,
    status: NoteStatus,
    error_message: Optional[str] = None,
    **kwargs: Any,
) -> None:
    """
    更新笔记状态与附加字段（白名单限制）

    在 Celery worker 的独立数据库会话中执行；状态写穿 Vault meta 镜像。

    Args:
        note_id: 笔记 ID
        status: 新的状态
        error_message: 错误信息（可选）
        **kwargs: 白名单内的附加字段；metadata_ 做字段级合并（F-27 修复：
                  整包替换会丢失 clean_task_id 等任务生命周期字段）
    """
    session_factory = get_sync_session()
    async with session_factory() as session:
        result = await session.execute(select(Note).where(Note.id == note_id))
        note = result.scalars().first()
        if not note:
            return

        note.status = status
        if error_message:
            note.error_message = error_message
        for key, value in kwargs.items():
            if key not in _UPDATABLE_NOTE_FIELDS:
                continue
            if key == "metadata_" and isinstance(value, dict) and note.metadata_:
                # metadata 字段级合并：保留旧字段，仅覆盖新字段
                merged = dict(note.metadata_)
                merged.update(value)
                note.metadata_ = merged
            else:
                setattr(note, key, value)
        await session.commit()
        await session.refresh(note)
        # 状态写穿镜像：同步更新 Vault output/meta/{base}.json
        write_note_meta(note)
