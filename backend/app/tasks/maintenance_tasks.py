"""
运维/维护类 Celery 任务

目前包含**僵尸任务自愈**（阶段 1′ 1.8）。

## 为什么需要自愈任务

文件系统 broker 没有 visibility timeout，`task_acks_late=True` 在它上面
也拿不到"未确认即重投"的语义。worker 进程崩溃（OOM、强杀、断电）时：

- Celery 侧：消息永久停在未确认状态，不会重投
- 应用侧：笔记永久停在 converting / cleaning / learning

结果是用户看到一个永远转圈的界面，既拿不到失败提示，也没有重试入口。
靠 DB 侧的心跳超时来兜底，是这条技术路线下唯一可行的补偿手段。
"""

import asyncio
import logging

from .celery_app import celery_app
from .loop import run_async, task_loop

logger = logging.getLogger(__name__)

#: 心跳超过该秒数判定为僵尸。与 task_run_service 的默认值保持一致；
#: 放在这里是为了让 Beat 的调度周期可以独立于服务层默认值调整。
STALE_AFTER_SECONDS = 900


@celery_app.task(name="app.tasks.maintenance_tasks.reap_stale_tasks")
def reap_stale_tasks_task() -> dict:
    """扫描心跳超时的任务，标记为 stale 并释放被卡住的笔记

    由 Beat 周期触发（见 celery_app.beat_schedule）。幂等：
    已经处于终态的任务不会被重复处理。
    """
    from ..services.task_run_service import reap_stale_tasks

    with task_loop("reap_stale_tasks"):
        result = run_async(reap_stale_tasks(STALE_AFTER_SECONDS))
    if result["reaped"]:
        logger.warning(
            "僵尸任务自愈完成: 扫描 %d，标记 %d，释放笔记 %d",
            result["scanned"], result["reaped"], result["notes_released"],
        )
    return result


@celery_app.task(name="app.tasks.maintenance_tasks.backup_database")
def backup_database_task() -> dict:
    """每日数据库快照（阶段 1′ 第 5 项）

    机制此前已具备（`scripts/backup_db.py`），但只能靠人记得手动跑 ——
    而"需要人记得"的备份等于没有备份。这里挂到 Beat 上，并带上保留策略，
    使 data 目录不会无限增长。

    刻意不抛异常：备份失败必须留下可观测的记录（返回体里的 error 字段
    与 error 级日志），但不应该让 Beat 任务堆积失败状态。
    """
    from ..services.backup_service import run_scheduled_backup

    with task_loop("backup_database"):
        result = run_async(_backup_in_thread(run_scheduled_backup))
    if not result.get("ok"):
        logger.error("每日备份未成功: %s", result)
    else:
        logger.info(
            "每日备份完成: %s | integrity=%s | 保留策略清理 %d 份",
            result.get("snapshot"), result.get("integrity"),
            len(result.get("pruned") or []),
        )
    return result


async def _backup_in_thread(func) -> dict:
    """在线程池里跑同步的备份逻辑，避免阻塞 worker 的事件循环

    `VACUUM INTO` 是同步 sqlite3 调用，大库上可能耗时数秒到数十秒。
    直接在事件循环里跑会卡住同一 worker 上排队的其他协程。
    """
    return await asyncio.to_thread(func, "daily")
