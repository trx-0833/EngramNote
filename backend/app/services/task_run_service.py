"""
任务运行追踪服务

为 Celery 任务提供"可查询的生命周期"：进度、阶段名、心跳、失败原因，
以及**僵尸任务自愈**（阶段 1′ 1.7 / 1.8）。

## 为什么需要它

文件系统 broker 没有 visibility timeout（Redis / SQS 才有）。任务被 worker
取走后若 worker 崩溃，消息永久停在"未确认"状态，而笔记则永久卡在
converting / cleaning / learning —— UI 上表现为无限转圈，用户既等不到结果
也无法重试。本模块用 DB 侧的心跳 + 超时扫描来补偿 broker 的这个缺失。

## 会话从哪来

任务侧走 `app/tasks/common.py::get_sync_session()`（worker 进程级单例引擎），
API 侧走 FastAPI 依赖注入的会话。两条路都用**同一个** URL，
因此看到的是同一份数据。

注意不要把 `app/database.py::get_session_factory()` 用在任务里：
任务用 `asyncio.run()` 每次开新事件循环，而那个工厂绑定在 API 的循环上，
跨循环复用连接池会出问题（这正是 tasks/common.py 自带引擎的原因）。
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.note import Note, NoteStatus
from ..models.task_run import (
    ACTIVE_TASK_STATUSES,
    ERROR_MAX_LEN,
    TERMINAL_TASK_STATUSES,
    TaskRun,
    TaskStatus,
)
from ..tasks.common import get_sync_session

logger = logging.getLogger(__name__)

#: 心跳超过该秒数即视为僵尸（阶段 1′ 1.8）
#:
#: 取值依据：任务在上报进度时会刷新心跳，而两次上报之间的间隔是
#: "阶段"粒度（解析、清洗、抽取），实测最长阶段（嵌入模型首次加载 +
#: 大文档抽取）在分钟级。默认 15 分钟既不会误杀正常长任务，
#: 又能在 worker 崩溃后的一刻钟内让用户看到可重试的失败态。
DEFAULT_STALE_AFTER_SECONDS = 900

#: 任务名 → （笔记应处于的"进行中"状态，可重试的失败文案）
#:
#: 僵尸收尾时用它判断"这条笔记是不是被这个任务卡住了"，避免误改
#: 已经被后续任务推进过的笔记。用前缀匹配（任务名以模块路径开头）。
_TASK_NOTE_STAGE: tuple[tuple[str, NoteStatus, str], ...] = (
    (
        "convert_document_task",
        NoteStatus.converting,
        "文档转换任务已中断（worker 异常退出）。可删除后重新上传，或对该笔记重试转换。",
    ),
    (
        "clean_document_task",
        NoteStatus.cleaning,
        "文档清洗任务已中断（worker 异常退出）。可对该笔记重试清洗。",
    ),
    (
        "understand_document_task",
        NoteStatus.learning,
        "理解任务已中断（worker 异常退出）。可对该笔记重试理解。",
    ),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _truncate_error(error: Optional[str]) -> Optional[str]:
    """截断错误摘要：完整堆栈进日志文件，数据库只留可读的一段"""
    if not error:
        return None
    if len(error) <= ERROR_MAX_LEN:
        return error
    return error[:ERROR_MAX_LEN] + f"...（已截断，完整内容见日志，原始长度 {len(error)}）"


def task_name_to_note_stage(task_name: str) -> Optional[tuple[NoteStatus, str]]:
    """把任务名映射为"它会让笔记停在哪个进行中状态"及可重试文案"""
    for marker, status, message in _TASK_NOTE_STAGE:
        if marker in task_name:
            return status, message
    return None


# ---------------------------------------------------------------------------
# 任务侧：写入路径（用 worker 自己的会话工厂）
# ---------------------------------------------------------------------------

async def ensure_task_run(
    task_id: str,
    task_name: str,
    *,
    note_id: Optional[str] = None,
    user_id: Optional[str] = None,
    max_attempts: int = 3,
    stage: str = "",
) -> Optional[TaskRun]:
    """创建任务记录；已存在则复用并递增 attempt（重试场景）

    幂等：以 task_id 为键。重试时 Celery 会复用同一个 task_id
    （`self.retry()` 不换 id），因此这里是"递增尝试次数"而非新增行 ——
    否则"这张笔记现在在做什么"会有多行互相矛盾的答案。

    Returns:
        写入后的 TaskRun；数据库不可用时返回 None（**不抛异常**：
        任务追踪失败不应该让业务任务本身失败）
    """
    try:
        session_factory = get_sync_session()
        async with session_factory() as session:
            existing = (await session.execute(
                select(TaskRun).where(TaskRun.task_id == task_id)
            )).scalars().first()

            if existing is not None:
                # 重试路径：同一 task_id 再次被执行
                existing.attempt += 1
                existing.status = TaskStatus.running
                existing.heartbeat_at = _now()
                existing.started_at = existing.started_at or _now()
                existing.finished_at = None
                if stage:
                    existing.stage = stage
                existing.error = None
                await session.commit()
                await session.refresh(existing)
                return existing

            run = TaskRun(
                task_id=task_id,
                task_name=task_name,
                note_id=note_id,
                user_id=user_id,
                status=TaskStatus.running,
                progress=0.0,
                stage=stage,
                attempt=1,
                max_attempts=max_attempts,
                heartbeat_at=_now(),
                started_at=_now(),
            )
            session.add(run)
            await session.commit()
            await session.refresh(run)
            return run
    except Exception as exc:
        logger.warning("创建任务记录失败（不影响任务本身）: task_id=%s, %s", task_id, exc)
        return None


async def report_progress(
    task_id: str,
    progress: Optional[float] = None,
    *,
    stage: Optional[str] = None,
    message: Optional[str] = None,
) -> None:
    """上报进度并刷新心跳

    Args:
        progress: 0.0~1.0；越界会被夹到区间内（宁可显示 100% 也不要越界值
                  让前端进度条画到容器外）
        stage: 阶段可读名（直接展示给用户）
        message: 附加说明
    """
    try:
        session_factory = get_sync_session()
        async with session_factory() as session:
            run = (await session.execute(
                select(TaskRun).where(TaskRun.task_id == task_id)
            )).scalars().first()
            if run is None:
                return
            # 终态不再接受进度上报（迟到的上报不该把已完成的任务改回 running）
            if run.status in TERMINAL_TASK_STATUSES:
                return
            if progress is not None:
                run.progress = max(0.0, min(1.0, float(progress)))
            if stage is not None:
                run.stage = stage
            if message is not None:
                run.message = message
            run.status = TaskStatus.running
            run.heartbeat_at = _now()
            await session.commit()
    except Exception as exc:
        logger.debug("上报任务进度失败（忽略）: task_id=%s, %s", task_id, exc)


async def mark_succeeded(task_id: str, *, stage: Optional[str] = None) -> None:
    """标记任务成功（进度置 1.0）"""
    await _finish(task_id, TaskStatus.succeeded, progress=1.0, stage=stage)


async def mark_failed(task_id: str, error: Optional[str]) -> None:
    """标记任务失败（写入截断后的错误摘要）"""
    await _finish(task_id, TaskStatus.failed, error=error)


async def mark_cancelled(task_id: str, reason: Optional[str] = None) -> None:
    """标记任务被取消"""
    await _finish(task_id, TaskStatus.cancelled, error=reason)


async def _finish(
    task_id: str,
    status: TaskStatus,
    *,
    progress: Optional[float] = None,
    stage: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    try:
        session_factory = get_sync_session()
        async with session_factory() as session:
            run = (await session.execute(
                select(TaskRun).where(TaskRun.task_id == task_id)
            )).scalars().first()
            if run is None:
                return
            run.status = status
            run.finished_at = _now()
            run.heartbeat_at = _now()
            if progress is not None:
                run.progress = max(0.0, min(1.0, float(progress)))
            if stage is not None:
                run.stage = stage
            if error is not None:
                run.error = _truncate_error(error)
            await session.commit()
    except Exception as exc:
        logger.warning("结束任务记录失败（忽略）: task_id=%s, %s", task_id, exc)


# ---------------------------------------------------------------------------
# 僵尸任务自愈（阶段 1′ 1.8）
# ---------------------------------------------------------------------------

async def find_stale_tasks(
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
    limit: int = 100,
) -> Sequence[TaskRun]:
    """找出心跳超时且仍处于进行中的任务记录

    **返回的对象是 detached 的**（所属 session 已关闭，且显式 expunge）。
    这意味着：修改它们再 commit 是**静默无效**的 —— 不会报错，也不会写库。

    之所以显式 expunge 而不是放任其自然失效：本轮实测踩到过这个坑 ——
    扫描与修改分属两个 session，结果 `status` 改了、`commit()` 也调了，
    数据库里却仍是 running，而日志显示"已标记 1 个"。显式 detach 让
    "这些对象只能读"成为明确的契约，而不是隐蔽的陷阱。

    调用方若要修改，必须在**同一个 session 内重新查询**（见 reap_stale_tasks）。
    """
    cutoff = _now() - timedelta(seconds=stale_after_seconds)
    session_factory = get_sync_session()
    async with session_factory() as session:
        rows = await session.execute(
            select(TaskRun)
            .where(
                TaskRun.status.in_(ACTIVE_TASK_STATUSES),
                # heartbeat_at 为 NULL 说明记录创建后从未心跳过，同样按超时处理
                (TaskRun.heartbeat_at.is_(None)) | (TaskRun.heartbeat_at < cutoff),
            )
            .order_by(TaskRun.heartbeat_at.asc().nulls_first())
            .limit(limit)
        )
        runs = list(rows.scalars().all())
        # 显式脱离 session：让"只能读"成为契约而非隐患
        session.expunge_all()
        return runs


async def reap_stale_tasks(
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
) -> dict[str, Any]:
    """把心跳超时的任务标记为 stale，并**释放**被它卡住的笔记

    收尾两件事：
    1. `task_runs.status = stale`（用户可见"可重试"）
    2. 若该任务对应的笔记仍停在它设置的"进行中"状态，改判为 failed
       并写入可操作的错误信息。

    第 2 步用 `task_name_to_note_stage()` 做**双重校验**：
    只有当笔记确实卡在这个任务负责的那个状态时才动它。否则
    （例如笔记已被后续任务推进到 cleaned）保持原样 —— 盲目改状态
    会造成"已完成的笔记被标成失败"。

    Returns:
        {"scanned": n, "reaped": n, "notes_released": n, "task_ids": [...]}
    """
    cutoff = _now() - timedelta(seconds=stale_after_seconds)
    reaped_ids: list[str] = []
    notes_released = 0

    session_factory = get_sync_session()
    async with session_factory() as session:
        # 查询与修改必须在**同一个 session** 内：分开写会让 ORM 对象在
        # 第二个 session 里处于 detached 状态，赋值 + commit 静默不生效
        rows = await session.execute(
            select(TaskRun)
            .where(
                TaskRun.status.in_(ACTIVE_TASK_STATUSES),
                (TaskRun.heartbeat_at.is_(None)) | (TaskRun.heartbeat_at < cutoff),
            )
            .order_by(TaskRun.heartbeat_at.asc().nulls_first())
            .limit(100)
        )
        stale = list(rows.scalars().all())
        if not stale:
            return {"scanned": 0, "reaped": 0, "notes_released": 0, "task_ids": []}

        for run in stale:
            run.status = TaskStatus.stale
            run.finished_at = _now()
            run.error = _truncate_error(
                f"任务心跳超时（超过 {stale_after_seconds} 秒无上报），"
                "判定为 worker 异常退出"
            )
            reaped_ids.append(run.task_id)

            stage = task_name_to_note_stage(run.task_name)
            if stage is None or not run.note_id:
                continue
            expected_status, retry_message = stage

            note = (await session.execute(
                select(Note).where(Note.id == run.note_id)
            )).scalars().first()
            if note is None or note.status != expected_status:
                continue

            note.status = NoteStatus.failed
            note.error_message = retry_message
            notes_released += 1

        await session.commit()

    logger.warning(
        "僵尸任务自愈: 扫描到 %d 个超时任务，标记 stale %d 个，释放笔记 %d 条 | task_ids=%s",
        len(stale), len(reaped_ids), notes_released,
        ",".join(t[:8] for t in reaped_ids[:10]),
    )
    return {
        "scanned": len(stale),
        "reaped": len(reaped_ids),
        "notes_released": notes_released,
        "task_ids": reaped_ids,
    }


# ---------------------------------------------------------------------------
# API 侧：读取路径（复用注入的会话，只读）
# ---------------------------------------------------------------------------

async def get_task_run(db: AsyncSession, task_id: str) -> Optional[TaskRun]:
    """按 task_id 查询任务记录"""
    return (await db.execute(
        select(TaskRun).where(TaskRun.task_id == task_id)
    )).scalars().first()


async def list_task_runs_for_note(
    db: AsyncSession, note_id: str, limit: int = 20,
) -> list[TaskRun]:
    """列出某笔记的任务记录（按创建时间倒序，最新在前）"""
    rows = await db.execute(
        select(TaskRun)
        .where(TaskRun.note_id == note_id)
        .order_by(TaskRun.created_at.desc())
        .limit(limit)
    )
    return list(rows.scalars().all())


async def request_cancel(db: AsyncSession, task_id: str) -> bool:
    """请求取消任务：标记 cancelled，并尽力撤销 Celery 侧的任务

    Returns:
        True 表示记录已更新（无论 Celery 侧撤销是否成功）

    说明：文件系统 broker **不支持**可靠的远端撤销 —— Celery 的
    `revoke()` 在文件传输下只能做到"标记撤销"，已在执行中的任务不会
    立刻停止。因此这里的态度是诚实的：把记录标为 cancelled 让 UI 不再
    转圈，并让任务自身在下一个进度上报点检查到"已被取消"而主动退出
    （见 report_progress 的终态保护 + 任务内的 cancel 检查）。
    不假装"已强杀"。
    """
    run = await get_task_run(db, task_id)
    if run is None:
        return False
    if run.status in TERMINAL_TASK_STATUSES:
        return True

    run.status = TaskStatus.cancelled
    run.finished_at = _now()
    run.error = "用户取消"
    await db.commit()

    try:
        from ..tasks.celery_app import celery_app
        celery_app.control.revoke(task_id, terminate=False)
    except Exception as exc:  # pragma: no cover - 取决于 broker 能力
        logger.info("Celery revoke 未生效（文件 broker 的正常行为）: %s", exc)

    return True


async def is_cancelled(task_id: str) -> bool:
    """任务内检查：是否已被用户取消（供长任务在阶段边界主动退出）"""
    try:
        session_factory = get_sync_session()
        async with session_factory() as session:
            status = (await session.execute(
                select(TaskRun.status).where(TaskRun.task_id == task_id)
            )).scalar()
            return status == TaskStatus.cancelled
    except Exception:
        return False


async def force_fail_note_if_stuck(
    db: AsyncSession, note_id: str, reason: str,
) -> int:
    """把卡在"进行中"状态的笔记改判为失败（供管理/自愈脚本使用）

    Returns:
        被改判的笔记数（0 或 1）
    """
    stuck_statuses = {s for _, s, _ in _TASK_NOTE_STAGE}
    result = await db.execute(
        update(Note)
        .where(Note.id == note_id, Note.status.in_(stuck_statuses))
        .values(status=NoteStatus.failed, error_message=reason)
    )
    await db.commit()
    return result.rowcount or 0
