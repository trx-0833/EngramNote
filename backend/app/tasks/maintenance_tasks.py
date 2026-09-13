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


@celery_app.task(name="app.tasks.maintenance_tasks.restore_drill")
def restore_drill_task() -> dict:
    """每周恢复演练：证明最近一份快照**真的能恢复**（阶段 6.6）

    ## 为什么"每天备份成功"不等于"有可用备份"

    `create_snapshot` 会校验**刚写下**的那一份。但快照在磁盘上放几周之后
    是否还能用（被截断、扇区损坏、被误改），以及备份是否**还在跑**
    （最新快照是三天前的还是三个月前的），这两件事只有真去读一次才知道。

    演练**不做恢复动作**（恢复是破坏性的，必须由人执行，
    见 `scripts/restore_db.py`），它只读地检查快照并报出与线上库的行数差异。

    ## 失败必须是 error 级

    演练失败意味着"当前没有可用的备份"—— 那是需要立刻有人处理的状况，
    而不是一条可以忽略的 warning。失败不抛异常（不阻塞 Beat），
    但日志级别与返回体都要能反映严重性。
    """
    from ..services.backup_service import run_restore_drill

    result = run_restore_drill()
    if not result.get("ok"):
        logger.error("恢复演练未通过: %s", result.get("reason") or result.get("problems"))
    return result


@celery_app.task(name="app.tasks.maintenance_tasks.vault_audit")
def vault_audit_task() -> dict:
    """每周存储审计：库里的路径与磁盘文件是否一致（阶段 6.6 的另一半）

    ## 为什么需要它（与恢复演练互补，不是重复）

    恢复演练回答的是"**备份**可用吗"，它只看数据库。
    而数据实际分布在两处：

        data/db/engramnote.db    元数据（笔记、卡片、题目、复习进度）
        data/storage/…           原始文件与 Markdown（库里的路径指向它们）

    只恢复数据库、丢掉了文件，用户看到的是"笔记都在，但每一篇都打不开" ——
    与丢了数据没有区别。这个审计补的就是"库 ↔ 磁盘"这一维。

    ## 为什么之前没跑过

    `audit_vault` 早就实现了（含缺失文件、孤儿文件、大小/哈希比对），
    测试也有 14 条 —— 但**只有 `scripts/verify_vault.py` 手工调用过**，
    没有任何调度。于是它在运行期等于不存在：真出问题时不会有人知道，
    除非恰好有人记得去跑那个脚本。

    （本轮已经是第三次遇到同一模式：AF.10 的账本清理、AU.1 的限流规则、
    这里。共同点是"工具写好了、没人调用"，而它不会报错、只会静默地不生效。）

    ## 只报"库→磁盘"方向 + 孤儿，不做哈希深比

    `deep=False`：哈希深比要读全部文件，在日频/周频任务里是浪费
    （磁盘静默损坏的概率远低于"文件被人手工删了"）。
    需要深比时用 `scripts/verify_vault.py --deep` 手工跑。
    """
    from ..database import get_session_factory
    from ..services.vault_audit_service import audit_vault

    async def _run() -> dict:
        factory = get_session_factory()
        async with factory() as db:
            result = await audit_vault(db, deep=False, include_orphans=True)
        return result.to_dict()

    try:
        with task_loop("vault_audit"):
            result = run_async(_run())
    except Exception as exc:  # noqa: BLE001 - 审计失败不阻塞 Beat
        logger.error("存储审计失败: %s", exc, exc_info=True)
        return {"ok": False, "error": str(exc)}

    if result.get("ok"):
        logger.info(
            "存储审计通过: 扫描 %d 篇笔记，%d 个对象全部存在",
            result.get("notes_scanned", 0), result.get("db_objects", 0),
        )
    else:
        # 不一致意味着"用户界面上能看到、但打不开"的内容已经存在 —— error 级
        logger.error(
            "存储审计发现不一致: %s | 扫描 %d 篇，DB 对象 %d，磁盘对象 %d",
            result.get("counts"), result.get("notes_scanned", 0),
            result.get("db_objects", 0), result.get("disk_objects", 0),
        )
    return result


async def _backup_in_thread(func) -> dict:
    """在线程池里跑同步的备份逻辑，避免阻塞 worker 的事件循环

    `VACUUM INTO` 是同步 sqlite3 调用，大库上可能耗时数秒到数十秒。
    直接在事件循环里跑会卡住同一 worker 上排队的其他协程。
    """
    return await asyncio.to_thread(func, "daily")


@celery_app.task(name="app.tasks.maintenance_tasks.cleanup_llm_ledger")
def cleanup_llm_ledger_task() -> dict:
    """清理 LLM 账本与响应缓存（附录 AF.10 那个"没人调用"的清理）

    ## 为什么必须有这个任务

    阶段 4.2 的 `llm_calls` 与 4.7 的 `llm_cache` 都只增不减：
    前者每次调用一行，后者每个不同的输入一行。清理函数
    （`purge_old_calls` / `purge_expired`）早就写好了，
    但**没有任何东西调用它们** —— 于是"写好了清理逻辑"这件事
    在运行期等于不存在，磁盘会一直涨（本项目曾因空间不足放弃 PG/Redis）。

    ## 两张表被删的东西性质完全不同，因此口径也不同

    | 表 | 内容 | 删除口径 |
    |---|---|---|
    | `llm_cache` | **可再生的派生数据**（同一个输入再问一次就有） | 过期即删（TTL 到期） |
    | `llm_calls` | **不可再生的账本**（花了多少钱的唯一记录） | 按保留期删，默认 365 天，配 0 则永久保留 |

    因此这里把两者的删除行数**分别**打日志：把"删掉了一年的账本"
    和"清掉了一堆过期缓存"混成一个数字，会让人无法判断该不该紧张。

    刻意不抛异常：清理失败不该让 Beat 堆积失败状态，
    但必须留下 error 级日志（否则"清理没生效"这件事又会静默）。
    """
    from ..config import get_settings
    from ..database import get_session_factory
    from ..services.llm_accounting_service import purge_old_calls
    from ..services.llm_cache_service import purge_expired

    settings = get_settings()
    retention_days = int(getattr(settings, "llm_call_retention_days", 365) or 0)

    async def _run() -> dict:
        factory = get_session_factory()
        async with factory() as db:
            expired_cache = await purge_expired(db)
            old_calls = await purge_old_calls(db, retention_days=retention_days)
        return {"expired_cache_rows": expired_cache, "old_call_rows": old_calls,
                "retention_days": retention_days}

    try:
        with task_loop("cleanup_llm_ledger"):
            result = run_async(_run())
    except Exception as exc:  # noqa: BLE001 - 清理失败不阻塞 Beat
        logger.error("LLM 账本/缓存清理失败: %s", exc, exc_info=True)
        return {"error": str(exc)}

    logger.info(
        "LLM 账本清理完成: 过期缓存 %d 行，超保留期记账 %d 行（保留 %d 天）",
        result["expired_cache_rows"], result["old_call_rows"], result["retention_days"],
    )
    return result
