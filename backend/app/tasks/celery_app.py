"""
Celery 应用配置模块

本模块负责创建和配置 Celery 异步任务应用实例，
用于处理文档转换等耗时操作，避免阻塞 HTTP 请求。

主要职责：
- 创建 Celery 应用实例
- 配置序列化方式、时区、任务追踪等参数
- 配置文件系统 broker 的数据目录
- 自动发现任务模块
- （可观测性）通过信号为每个任务注入 task_id/task_name 上下文，
  使任务内全部日志自动携带 tid/task 标签，并输出任务生命周期日志

设计决策：
- 默认使用文件系统作为 broker 和结果后端，实现零外部依赖启动
- 使用 JSON 序列化，确保任务参数和结果可读且跨语言兼容
- task_acks_late=True：任务完成后才确认，避免任务执行中 worker 崩溃导致任务丢失
- worker_prefetch_multiplier=1：每次只预取一个任务，避免长任务阻塞短任务
- 时区设为 Asia/Shanghai，但启用 UTC 以确保时间戳一致性
"""

import logging

from celery import Celery
from celery.schedules import crontab

from ..config import get_settings
from ..core import context
from ..core.logging_config import setup_logging
from ..core.tempfile_compat import apply_tempfile_compat

settings = get_settings()

# 运行环境兼容：替换 tempfile.mkdtemp（详见 core/tempfile_compat.py），
# 必须在任务模块导入前生效，确保 worker 内所有临时目录行为一致
apply_tempfile_compat()

# 文件系统 broker 的消息目录，确保启动前已创建
# 注意：Windows 上 kombu 文件系统传输的 data_folder_in 和 data_folder_out
# 必须指向同一目录，否则跨目录文件移动操作会失败（os.rename 不支持跨盘符）
#
# 必须走 settings.get_celery_broker_dir()：此前这里写的是
# `Path(settings.get_storage_dir().parent / "celery" / "broker")`，
# 而 get_celery_broker_url() 用的是 DATA_DIR —— 两者只在 storage_dir
# 未配置时才碰巧相等。配置了 storage_dir / vault_dir（部署常规做法）后，
# 这里会指向**用户主目录**，任务被投递到无人监听的目录。
_broker_dir = settings.get_celery_broker_dir()
_broker_dir.mkdir(parents=True, exist_ok=True)
_result_dir = settings.get_celery_result_dir()
_result_dir.mkdir(parents=True, exist_ok=True)

# 创建 Celery 应用实例
# broker: 消息队列，用于分发任务
# backend: 结果存储，用于查询任务状态和结果
celery_app = Celery(
    "engramnote",
    broker=settings.get_celery_broker_url(),
    backend=settings.get_celery_result_backend(),
)

celery_app.conf.update(
    # 序列化配置：统一使用 JSON，确保任务参数和结果可读且跨语言兼容
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # 时区配置：内部使用 UTC，显示使用上海时区
    timezone="Asia/Shanghai",
    enable_utc=True,
    # 任务追踪：记录任务开始时间，便于监控
    task_track_started=True,
    # 延迟确认：任务执行完成后才确认（而非接收后确认），
    # 防止 worker 崩溃时任务丢失
    task_acks_late=True,
    # ---- 任务可靠性（阶段 1′ 1.6）----
    #
    # 为什么必须在**全局**设置：文件系统 broker 没有 visibility timeout
    # （不像 Redis/SQS 有可见性超时），任务一旦被 worker 取走又没有 ack，
    # 就永久停在"未确认"状态——除非显式声明 reject_on_worker_lost。
    # 此前只有 embedding_tasks 的两个任务单独设了这个参数，
    # convert / clean / understand 三个**关键**任务（用户上传后必经的链路）
    # 都没设：worker 崩在转换中途 = 该笔记永久卡在 converting。
    task_reject_on_worker_lost=True,
    # 超时兜底：外部 API（Mineru / LLM / ASR）挂住不返回时，
    # 任务不能无限占用 worker。
    # soft 先抛出 SoftTimeLimitExceeded 让任务有机会收尾（写失败状态、清理临时文件），
    # hard 再强杀。两者差距留出收尾时间。
    #
    # 取值依据：实测转换链路的耗时量级 —— LLM 抽取约 7s、
    # 嵌入模型首次加载约数十秒、大 PDF 转换可达数分钟。
    # 30 分钟 hard / 28 分钟 soft 对小文件无影响，只兜住真正卡死的任务。
    task_time_limit=1800,
    task_soft_time_limit=1680,
    # 结果后端是文件系统：任务失败时不重试 ack，交由上面的 reject_on_worker_lost
    # 与僵尸任务自愈（reminder_tasks.reap_stale_tasks）兜底
    task_acks_on_failure_or_timeout=True,
    # 预取倍数：设为 1 表示每次只预取一个任务，
    # 避免长任务（如大文件转换）阻塞后续短任务
    worker_prefetch_multiplier=1,
    # 结果过期时间：1小时后自动清理结果文件，避免磁盘堆积
    result_expires=3600,
    # 文件系统 broker 配置：指定消息的输入/输出目录
    # Windows 上 in/out 必须相同，否则 os.rename 跨目录失败
    broker_transport_options={
        "data_folder_in": str(_broker_dir),
        "data_folder_out": str(_broker_dir),
    },
)

# 直接包含任务模块，注册所有 Celery 任务
# 注意：autodiscover_tasks 查找的是子包中的 tasks.py，
# 而我们的任务直接定义在 app.tasks.convert_tasks 中，
# 所以使用 include 参数显式导入
celery_app.conf.update(
    include=[
        "app.tasks.convert_tasks",
        "app.tasks.clean_tasks",
        "app.tasks.understand_tasks",
        "app.tasks.embedding_tasks",
        "app.tasks.reminder_tasks",
        "app.tasks.maintenance_tasks",
    ],
)

# Celery Beat 定时任务调度配置
# 时区已在上方设置为 Asia/Shanghai，crontab 将使用上海时间
celery_app.conf.update(
    beat_schedule={
        # 每日 00:30 刷新学习目标进度（活跃目标的 progress_cache、状态流转）
        "refresh-goal-progress-daily": {
            "task": "app.tasks.reminder_tasks.refresh_goal_progress",
            "schedule": crontab(hour=0, minute=30),
        },
        # 每日 09:00 发送复习提醒邮件（仅对开启邮件提醒的用户）
        "send-daily-review-email": {
            "task": "app.tasks.reminder_tasks.send_daily_review_email",
            "schedule": crontab(hour=9, minute=0),
        },
        # 每 5 分钟自愈一次僵尸任务（阶段 1′ 1.8）
        #
        # 周期取 5 分钟而非心跳超时（15 分钟）本身：心跳超时是"多久算死"，
        # 扫描周期是"多久检查一次"。扫描更频繁只是多几次廉价查询，
        # 却能让用户在被卡住后最多等 20 分钟就拿到可重试的失败态，
        # 而不是等到下一次日级调度。
        "reap-stale-tasks": {
            "task": "app.tasks.maintenance_tasks.reap_stale_tasks",
            "schedule": crontab(minute="*/5"),
        },
        # 每日 03:30 数据库快照（阶段 1′ 第 5 项）
        #
        # 选在凌晨且避开 00:30 的目标进度刷新：VACUUM INTO 会持有读事务，
        # 与写任务错开可减少 SQLite 单写者争用。
        # 保留份数由 settings.backup_keep 控制（默认 14 份 ≈ 两周）。
        "daily-database-backup": {
            "task": "app.tasks.maintenance_tasks.backup_database",
            "schedule": crontab(hour=3, minute=30),
        },
        # 每日 04:30 清理 LLM 账本与过期响应缓存
        #
        # 阶段 4.2/4.7 的两张表都只增不减，而清理函数此前**没有任何调用方**
        # （附录 AF.10）。排在备份之后：先留下快照，再删数据 ——
        # 顺序反了的话，"昨天备份里还有的账本"会在发现异常时已经无从对照。
        # 保留期由 settings.llm_call_retention_days 控制（默认 365 天，0 = 永久保留）。
        "cleanup-llm-ledger-daily": {
            "task": "app.tasks.maintenance_tasks.cleanup_llm_ledger",
            "schedule": crontab(hour=4, minute=30),
        },
        # 每周一 05:00 恢复演练（阶段 6.6）
        #
        # 每天备份成功 ≠ 有可用备份：快照放置几周后是否还能读、备份是否还在跑，
        # 只有真去读一次才知道。周期取"每周"而不是"每天"：
        # 演练要打开并深查一个 10MB 级的库，日频纯属浪费；而"备份坏了三周没人知道"
        # 与"备份坏了三天没人知道"，在恢复窗口上差别不大（有 14 份日备份兜底）。
        "restore-drill-weekly": {
            "task": "app.tasks.maintenance_tasks.restore_drill",
            "schedule": crontab(day_of_week=1, hour=5, minute=0),
        },
        # 每周一 05:30 存储审计：库里的路径 ↔ 磁盘文件（阶段 6.6 的另一半）
        #
        # 与恢复演练互补：演练只看数据库，而数据分布在 db + storage 两处。
        # 只恢复库、丢了文件，用户看到的是"笔记都在但每一篇都打不开"。
        # 排在演练之后：先确认备份可用，再核对线上的一致使性。
        # `audit_vault` 与它的 14 条测试早已存在，但此前**只有手工脚本调用**
        # （与 AF.10 的账本清理、AU.1 的限流规则同一种"写好了没人调用"）。
        "vault-audit-weekly": {
            "task": "app.tasks.maintenance_tasks.vault_audit",
            "schedule": crontab(day_of_week=1, hour=5, minute=30),
        },
    },
)


# ===========================================================================
# 可观测性：任务生命周期日志 + 任务上下文注入
# ===========================================================================
_task_logger = logging.getLogger("engramnote.task")


def _worker_init(sender=None, **_kwargs):
    """
    Worker 进程启动钩子：初始化统一日志配置 + 校验数据库 schema

    使 worker 中的业务日志与 FastAPI 进程使用完全一致的格式
    （上下文感知 + errors.log + JSON 日志）。

    为什么这里要校验 schema（阶段 1′ 1.9）：
    `init_db()` 此前**只在 `main.py` 启动时调用**（FastAPI 的 lifespan），
    worker 从不建表、也不检查。于是"先起 worker、后起 API"或
    "worker 独立部署"时，任务会在写入第一张表时静默失败，
    日志里只看到一句 no such table，排查方向完全指错。

    这里在 worker 启动阶段就显式建表/校验，失败则**记录 error 并继续**
    （不直接退出：worker 可能只是先于 API 启动，强行退出会让
    进程管理器陷入重启循环）。真正的可观测性由这条 error 日志承担。
    """
    setup_logging()
    _task_logger.info("Celery Worker 日志系统初始化完成")
    _ensure_worker_schema()


def _ensure_worker_schema() -> None:
    """在 worker 进程内确保数据库 schema 就绪（失败只告警，不中断启动）"""
    import asyncio

    try:
        from ..database import init_db

        asyncio.run(init_db())
        _task_logger.info("Worker 数据库 schema 校验完成")
    except Exception as exc:  # pragma: no cover - 依赖运行环境
        _task_logger.error(
            "Worker 数据库 schema 校验失败（任务可能在写入时报 no such table）: "
            "%s: %s",
            type(exc).__name__, exc,
        )


def _task_prerun(task_id, task, *args, **kwargs):
    """任务开始前：注入任务上下文（tid/task），后续日志自动携带"""
    context.reset_context()
    context.set_task_context(task_id=task_id, task_name=task.name)
    _task_logger.info("任务开始 | task=%s | task_id=%s", task.name, task_id)


def _task_postrun(task_id, task, retval, state, *args, **kwargs):
    """任务结束后：输出任务结果日志并清理上下文"""
    try:
        _task_logger.info(
            "任务结束 | task=%s | task_id=%s | state=%s | result=%s",
            task.name, task_id, state,
            str(retval)[:200] if retval is not None else "-",
        )
    except Exception:
        _task_logger.info("任务结束 | task=%s | task_id=%s | state=%s", task.name, task_id, state)
    finally:
        context.reset_context()


def _task_failure(task_id, task, einfo, *args, **kwargs):
    """任务失败：输出完整堆栈（含任务上下文标签，落 errors.log）"""
    exc = einfo.exception if einfo else None
    _task_logger.error(
        "任务失败 | task=%s | task_id=%s | type=%s | detail=%s",
        task.name, task_id, type(exc).__name__ if exc else "Unknown", exc,
        exc_info=einfo.exc_info if einfo else None,
    )


# 注册信号处理器
try:
    from celery.signals import task_postrun, task_prerun, task_failure, worker_process_init

    worker_process_init.connect(_worker_init)
    task_prerun.connect(_task_prerun)
    task_postrun.connect(_task_postrun)
    task_failure.connect(_task_failure)
except Exception:  # pragma: no cover - 信号注册失败不影响主流程
    logging.getLogger(__name__).warning("Celery 信号处理器注册失败", exc_info=True)
