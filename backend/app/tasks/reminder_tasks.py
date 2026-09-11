"""
复习提醒 Celery 任务模块

提供定时发送复习提醒邮件和刷新学习目标进度的能力。

主要职责：
- 每日定时（9:00）向开启邮件提醒的用户发送复习提醒邮件
- 每日定时（00:30）刷新所有学习目标的进度缓存

设计决策：
- SMTP 未配置时跳过邮件发送，仅记录 info 日志
- 任务使用 `task_loop()` + `run_async()` 包装异步服务调用（阶段 4.5）
- 单用户发送失败不影响其他用户
"""

import logging

from celery import shared_task
from sqlalchemy import select

from ..config import get_settings
from ..database import async_session
from ..models.user import User
from ..services.notification_service import NotificationService
from ..services.goal_service import goal_service
from .loop import run_async, task_loop

logger = logging.getLogger(__name__)
settings = get_settings()
notification_service = NotificationService()


@shared_task(name="app.tasks.reminder_tasks.send_daily_review_email")
def send_daily_review_email():
    """
    每日复习提醒邮件任务（Celery Beat 调度，每日 9:00 执行）

    查询所有 email_reminder_enabled=true 的用户，发送复习到期提醒邮件（未开启邮件提醒的用户由 SQL 过滤排除）。
    SMTP 未配置或用户无邮箱时跳过。

    Returns:
        str: 任务执行结果摘要（成功数/总数）
    """
    if not settings.email_reminder_enabled:
        logger.info("邮件提醒功能未开启，跳过发送")
        return "skipped: email_reminder_disabled"

    if not settings.smtp_host or not settings.smtp_user:
        logger.info("SMTP 未配置，跳过邮件发送")
        return "skipped: smtp_not_configured"

    async def _run():
        async with async_session() as db:
            # 仅查询开启邮件提醒的用户（email_reminder_enabled=True）
            # 同时过滤空邮箱：User.email 在模型中为 nullable=False，但仍需排除空字符串
            result = await db.execute(
                select(User).where(
                    User.email.isnot(None),
                    User.email != "",
                    User.email_reminder_enabled.is_(True),
                )
            )
            users = result.scalars().all()

            sent_count = 0
            for user in users:
                try:
                    ok = await notification_service.send_review_email(
                        user.email, user.id, db
                    )
                    if ok:
                        sent_count += 1
                except Exception as e:
                    # 单用户失败不影响其他用户
                    logger.warning(
                        f"发送邮件失败: user_id={user.id[:8]}, err={e}"
                    )

            logger.info(f"每日提醒邮件发送完成: 成功 {sent_count}/{len(users)}")
            return sent_count, len(users)

    try:
        with task_loop("send_daily_reminders"):
            sent, total = run_async(_run())
        return f"sent={sent}, total={total}"
    except Exception as e:
        logger.error(f"每日提醒邮件任务执行失败: {e}", exc_info=True)
        return f"error: {e}"


@shared_task(name="app.tasks.reminder_tasks.refresh_goal_progress")
def refresh_goal_progress_task():
    """
    学习目标进度刷新任务（Celery Beat 调度，每日 00:30 执行）

    调用 goal_service.refresh_goal_progress() 刷新所有 active 目标的进度缓存。
    该方法自建异步会话，独立于请求生命周期。

    Returns:
        str: 任务执行结果摘要
    """
    async def _run():
        await goal_service.refresh_goal_progress()

    try:
        with task_loop("refresh_goal_progress"):
            run_async(_run())
        logger.info("学习目标进度刷新完成")
        return "ok"
    except Exception as e:
        logger.error(f"学习目标进度刷新失败: {e}", exc_info=True)
        return f"error: {e}"
