"""
Celery 任务公共模块（收敛重复的连接工厂与状态更新，见 docs/decisions.md#F-27）

收敛 4 个任务模块中重复的：
- 独立数据库引擎/会话工厂（convert/clean/understand/embedding 各一份 → 此处一份）
- 笔记状态更新 `_update_note_status`（三份 → 此处一份，统一白名单 + metadata merge）
- 笔记状态查询 `_get_note_status`

注意：Celery worker 运行在独立进程中，需要自己的数据库连接；
模块级单例引擎在整个 worker 生命周期复用。
"""

import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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

    注意 URL 取自模块级 `settings`。`settings` 在测试换库时会被
    conftest 重新指向新实例（见 `reset_sync_session`），因此这里每次
    创建引擎都重新读 `settings.get_database_url()` 而不是缓存 URL 字符串。
    """
    global _sync_engine, _sync_session_factory
    if _sync_session_factory is None:
        db_url = settings.get_database_url()
        _sync_engine = create_async_engine(db_url, echo=False)
        if db_url.startswith("sqlite"):
            from ..database import register_sqlite_pragmas
            register_sqlite_pragmas(_sync_engine)
        _sync_session_factory = async_sessionmaker(_sync_engine, expire_on_commit=False)
    return _sync_session_factory


async def reset_sync_session() -> None:
    """
    丢弃 worker 侧引擎/会话工厂缓存（**测试隔离专用**）

    为什么必须有这个入口：`_sync_engine` / `_sync_session_factory` 是
    模块级单例，一旦建立就永久指向创建时的数据库。测试 fixture 通过换
    `DATABASE_URL` 实现隔离时，若不重置这里，任务侧的读写会**落到真实库**
    —— 与 `database.py` 那个"import 时冻结 database_url"的缺陷是同一类问题，
    而那一个已经造成过"API 测试一直在写生产库"的后果。

    Returns:
        None
    """
    global _sync_engine, _sync_session_factory
    if _sync_engine is not None:
        try:
            await _sync_engine.dispose()
        except Exception:  # pragma: no cover - 清理失败不应影响调用方
            pass
    _sync_engine = None
    _sync_session_factory = None
    _PROGRESS_CACHE.clear()


async def get_note_status(note_id: str) -> Optional[NoteStatus]:
    """查询笔记当前状态（用于停止/删除检查）"""
    session_factory = get_sync_session()
    async with session_factory() as session:
        result = await session.execute(select(Note).where(Note.id == note_id))
        note = result.scalars().first()
        return note.status if note else None


# ===========================================================================
# 进度上报（阶段 1′ 1.7）
# ===========================================================================
# 记忆上一次上报的进度，用于去重。
#
# 为什么要去重：SQLite 只有一个写者，而心跳/进度是"每条流水线都要写"的高频
# 写入。若每个阶段边界都无条件写一次，任务链路的写次数会成倍增长，
# 反而加剧单写者争用。这里只在上报点确实推进了（或阶段名变了）时才落库；
# 即便如此，重复调用同一进度仍会刷新心跳 —— 那是"worker 还活着"的信号，
# 不能被去重掉。
#
# 键是 task_id，值是上次落库的 (progress, stage)。worker 进程内缓存，
# 不同任务互不影响；进程重启后缓存清空，最多多写一次。
_PROGRESS_CACHE: dict[str, tuple[float, str]] = {}


async def report_task_progress(
    task_id: Optional[str],
    progress: float,
    stage: str,
    *,
    message: Optional[str] = None,
) -> None:
    """上报任务进度并刷新心跳（幂等、失败不影响业务）

    Args:
        task_id: Celery 任务 ID；为空时直接返回（任务未接入追踪时不产生开销）
        progress: 0.0~1.0
        stage: 阶段的可读名称，直接展示给用户
        message: 附加说明
    """
    if not task_id:
        return

    clamped = max(0.0, min(1.0, float(progress)))
    previous = _PROGRESS_CACHE.get(task_id)
    if previous == (clamped, stage):
        # 进度与阶段都没变：跳过写库。心跳的刷新由 _finish/_heartbeat 负责，
        # 长时间停留在一个阶段的任务由心跳保活逻辑兜底。
        return

    try:
        from ..services.task_run_service import report_progress

        await report_progress(task_id, clamped, stage=stage, message=message)
        _PROGRESS_CACHE[task_id] = (clamped, stage)
    except Exception as exc:
        # 追踪失败不能让业务任务失败
        logger.debug("上报任务进度失败（忽略）: task_id=%s, %s", task_id, exc)


def clear_progress_cache(task_id: Optional[str]) -> None:
    """任务结束时清理进度缓存，避免 worker 长驻时字典无界增长"""
    if task_id:
        _PROGRESS_CACHE.pop(task_id, None)


def task_id_of(celery_task) -> Optional[str]:
    """安全取出 Celery 任务的 task_id（任务未绑定 self 时返回 None）

    做成公开函数是为了让任务模块不必重复写
    `getattr(getattr(self, "request", None), "id", None)` 这串易错代码，
    也避免任务模块为了拿 task_id 而直接依赖 Celery 的内部结构。
    """
    return getattr(getattr(celery_task, "request", None), "id", None)


# ===========================================================================
# 任务生命周期包装（三个阶段任务共用，避免三份重复实现）
# ===========================================================================

def begin_task_run(celery_task, note_id: Optional[str] = None) -> bool:
    """任务入口：建立/复用 task_runs 记录，并检查是否已被取消

    用 `asyncio.run()` 在同步的 Celery 任务体里执行一次异步写入。
    与既有任务一致（它们本就在任务体里多次 `asyncio.run()`）。

    Returns:
        True  → 继续执行任务
        False → 任务已被用户取消，调用方应立即 return

    说明：返回 False 而不抛异常，是为了让任务"干净地结束"而不是以失败
    出现在 Celery 结果里 —— 用户主动取消不是故障。
    """
    import asyncio

    task_id = getattr(getattr(celery_task, "request", None), "id", None)
    if not task_id:
        return True

    async def _prepare() -> bool:
        from ..services.task_run_service import ensure_task_run, is_cancelled

        if await is_cancelled(task_id):
            logger.info("任务已被取消，跳过执行: task_id=%s", task_id)
            return False

        await ensure_task_run(
            task_id,
            celery_task.name,
            note_id=note_id,
            max_attempts=(getattr(celery_task, "max_retries", 0) or 0) + 1,
        )
        return True

    try:
        return asyncio.run(_prepare())
    except Exception as exc:
        logger.warning("建立任务记录失败（继续执行任务）: task_id=%s, %s", task_id, exc)
        return True


def mark_task_succeeded(celery_task) -> None:
    """任务成功结束：写终态（含失败时的进度缓存清理）"""
    import asyncio

    task_id = task_id_of(celery_task)
    if not task_id:
        return
    try:
        from ..services.task_run_service import mark_succeeded

        asyncio.run(mark_succeeded(task_id))
    except Exception as exc:
        logger.debug("标记任务成功失败（忽略）: %s", exc)


def mark_task_failed(celery_task, error: Optional[str]) -> None:
    """任务失败结束（含重试耗尽）：写终态与截断后的错误摘要"""
    import asyncio

    task_id = task_id_of(celery_task)
    if not task_id:
        return
    try:
        from ..services.task_run_service import mark_failed

        asyncio.run(mark_failed(task_id, error))
    except Exception as exc:
        logger.debug("标记任务失败失败（忽略）: %s", exc)


def mark_task_cancelled(celery_task, reason: Optional[str] = None) -> None:
    """任务主动放弃（用户停止、前置条件已不成立）：写 cancelled 终态

    与 failed 区分开：用户主动停止不是故障，UI 不应显示成红色错误，
    也不应计入失败率。
    """
    import asyncio

    task_id = task_id_of(celery_task)
    if not task_id:
        return
    try:
        from ..services.task_run_service import mark_cancelled

        asyncio.run(mark_cancelled(task_id, reason))
    except Exception as exc:
        logger.debug("标记任务取消失败（忽略）: %s", exc)


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
        **kwargs: 白名单内的附加字段；metadata_ 做字段级合并（
                  整包替换会丢失 clean_task_id 等任务生命周期字段，见 docs/decisions.md#F-27）
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
