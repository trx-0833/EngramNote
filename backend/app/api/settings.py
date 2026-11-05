"""用户设置接口（PC 设置页）

## 为什么是聚合端点

设置页一次要读语言、学习、提醒、账号与版本；如果让前端逐项调用，会出现
"打开页面打 5 个请求、其中一个失败整页半残"。这里用一个 `GET` 返回完整快照，
`PATCH` 只提交改动段，返回合并后的完整快照。

## 与旧端点的关系

`PUT /auth/locale` 与 `PUT /auth/reminder-settings` 继续保留（登录页/侧栏在用），
它们与这里的写路径指向同一批列。设置页走聚合端点，但不另起一套存储。
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_db
from ..models.note import Note
from ..models.user import User
from ..schemas.settings import (
    AboutSettings,
    AccountSettings,
    ReminderSettings,
    SettingsPatch,
    SettingsResponse,
    SettingsUsageResponse,
    StorageUsage,
    StudySettings,
)
from ..services import user_settings_service
from ..version import __version__
from .auth import get_current_user_dependency

router = APIRouter()


def _email_configured() -> bool:
    """部署侧是否具备发信能力（全局开关 + SMTP 两项都配了）"""
    settings = get_settings()
    return bool(
        settings.email_reminder_enabled
        and settings.smtp_host
        and settings.smtp_user
    )


async def _build_response(db: AsyncSession, user: User) -> SettingsResponse:
    values = await user_settings_service.get_effective_settings(db, user.id)
    settings = get_settings()
    return SettingsResponse(
        locale=user.locale,
        study=StudySettings(daily_review_limit=values["daily_review_limit"]),
        reminders=ReminderSettings(
            email_enabled=bool(user.email_reminder_enabled),
            email_hour=values["email_hour"],
            email_configured=_email_configured(),
        ),
        account=AccountSettings(email=user.email, username=user.username),
        about=AboutSettings(
            app_version=__version__,
            app_env=settings.app_env,
        ),
    )


@router.get("", response_model=SettingsResponse)
async def get_settings_snapshot(
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """读取当前用户的完整设置快照（不写库）"""
    return await _build_response(db, current_user)


@router.patch("", response_model=SettingsResponse)
async def patch_settings(
    req: SettingsPatch,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """部分更新设置；只提交显式给出的字段

    - `study.daily_review_limit` 写入 `user_settings`
    - `reminders.email_enabled` 写入 `users.email_reminder_enabled`
    - `reminders.email_hour` 写入 `user_settings`
    """
    daily_review_limit = req.study.daily_review_limit if req.study else None
    email_hour = req.reminders.email_hour if req.reminders else None
    email_enabled = req.reminders.email_enabled if req.reminders else None

    if daily_review_limit is not None or email_hour is not None:
        await user_settings_service.update_effective_settings(
            db,
            current_user.id,
            daily_review_limit=daily_review_limit,
            email_hour=email_hour,
        )

    if email_enabled is not None:
        current_user.email_reminder_enabled = bool(email_enabled)
        await db.commit()
        await db.refresh(current_user)

    return await _build_response(db, current_user)


@router.get("/usage", response_model=SettingsUsageResponse)
async def get_settings_usage(
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """存储用量（与上传前的配额校验同一口径：`SUM(Note.file_size)`）"""
    settings = get_settings()
    used = await db.execute(
        select(func.coalesce(func.sum(Note.file_size), 0)).where(
            Note.user_id == current_user.id
        )
    )
    used_bytes = int(used.scalar_one() or 0)
    quota_bytes = max(0, int(settings.max_storage_per_user_mb)) * 1024 * 1024
    return SettingsUsageResponse(
        storage=StorageUsage(used_bytes=used_bytes, quota_bytes=quota_bytes)
    )
