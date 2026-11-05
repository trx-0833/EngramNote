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
from datetime import datetime, timezone
from typing import Optional

from celery import shared_task
from sqlalchemy import select

from ..config import get_settings
from ..database import async_session
from ..models.user import User
from ..models.user_settings import UserSettings
from ..services.notification_service import NotificationService
from ..services.goal_service import goal_service
from ..utils.timeutil import BUSINESS_TZ, business_date_str
from .loop import run_async, task_loop

logger = logging.getLogger(__name__)
settings = get_settings()
notification_service = NotificationService()


def current_business_hour(now: Optional[datetime] = None) -> int:
    """业务时区（Asia/Shanghai）的当前小时，0-23"""
    moment = now or datetime.now(timezone.utc)
    return moment.astimezone(BUSINESS_TZ).hour


def should_send_now(
    user: User,
    user_settings: Optional[UserSettings],
    now: Optional[datetime] = None,
) -> bool:
    """这一刻是否该给该用户发提醒

    两条规则：

    1. **小时匹配**：用户设置的小时（没有行时回落到环境默认）等于业务时区
       当前小时。Beat 现在每小时跑一次，所以这是"按用户时间发送"的实现点。
    2. **当日去重**：`last_reminded_at` 的业务日期已经是今天就不再发。
       没有这条，任务重试或同一小时重复调度会发第二封。
    """
    moment = now or datetime.now(timezone.utc)
    preferred_hour = (
        int(user_settings.email_hour)
        if user_settings is not None
        else int(settings.email_reminder_hour)
    )
    if preferred_hour != current_business_hour(moment):
        return False
    if user.last_reminded_at is not None:
        if business_date_str(user.last_reminded_at) == business_date_str(moment):
            return False
    return True


async def select_due_recipients(db, now: Optional[datetime] = None):
    """按 `email_hour` 与当日去重选出这一小时该发的用户

    抽出来是为了能在测试里直接建库、造用户，验证 SQL + 时区 + 去重
    三件事一起工作，而不是只测 `should_send_now()` 这个纯函数。
    """
    moment = now or datetime.now(timezone.utc)
    result = await db.execute(
        select(User, UserSettings)
        .outerjoin(UserSettings, UserSettings.user_id == User.id)
        .where(
            User.email.isnot(None),
            User.email != "",
            User.email_reminder_enabled.is_(True),
        )
    )
    return [
        (user, pref)
        for user, pref in result.all()
        if should_send_now(user, pref, moment)
    ]


@shared_task(name="app.tasks.reminder_tasks.send_daily_review_email")
def send_daily_review_email():
    """
    每日复习提醒邮件任务（Celery Beat **每小时**调度）

    查询所有 email_reminder_enabled=true 的用户，再按每个用户的 `email_hour`
    过滤出"这一小时该发"的人；同一业务日已发过（`last_reminded_at`）的跳过。
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
            now = datetime.now(timezone.utc)
            rows = await select_due_recipients(db, now)

            sent_count = 0
            for user, _user_settings in rows:
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

            logger.info(f"每日提醒邮件发送完成: 成功 {sent_count}/{len(rows)}")
            return sent_count, len(rows)

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
