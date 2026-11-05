"""用户级设置的读写与默认值合并

## 默认值从哪来

`get_settings()`（环境配置）是**部署默认值**，用户行是覆盖层。读取顺序固定为：

    用户覆盖 → 环境默认 → 代码默认（模型列默认）

这样改 `.env` 的默认值仍然会影响"没设置过的用户"，而设置页不会变成第二套
配置系统。

## 为什么没有行时不写库

`GET /api/settings` 是读操作；打开设置页就产生一次写入会在 SQLite 单写者
模型下与真正的工作负载争锁。只有在 `update_settings()` 时才创建行。
"""

from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models.user_settings import UserSettings


def deployment_defaults() -> Dict[str, int]:
    """环境配置里的默认值（设置页未覆盖时使用）"""
    settings = get_settings()
    return {
        "daily_review_limit": int(settings.daily_review_limit),
        "email_hour": int(settings.email_reminder_hour),
    }


def _effective(row: Optional[UserSettings]) -> Dict[str, int]:
    values = deployment_defaults()
    if row is not None:
        values["daily_review_limit"] = int(row.daily_review_limit)
        values["email_hour"] = int(row.email_hour)
    return values


async def get_settings_row(
    db: AsyncSession, user_id: str
) -> Optional[UserSettings]:
    """取用户的设置覆盖行；没有则返回 None（调用方应使用默认值）"""
    result = await db.execute(
        select(UserSettings).where(UserSettings.user_id == user_id)
    )
    return result.scalars().first()


async def get_effective_settings(db: AsyncSession, user_id: str) -> Dict[str, int]:
    """读取合并后的有效设置（只读，不写库）"""
    return _effective(await get_settings_row(db, user_id))


async def update_effective_settings(
    db: AsyncSession,
    user_id: str,
    *,
    daily_review_limit: Optional[int] = None,
    email_hour: Optional[int] = None,
) -> Dict[str, int]:
    """部分更新；没有行时创建。返回更新后的完整有效值。

    只更新显式传入的字段（`None` = 不改），这样 PATCH 的语义与
    "缺省字段保持原值"一致。
    """
    row = await get_settings_row(db, user_id)
    if row is None:
        row = UserSettings(
            user_id=user_id,
            **deployment_defaults(),
        )
        db.add(row)

    if daily_review_limit is not None:
        row.daily_review_limit = int(daily_review_limit)
    if email_hour is not None:
        row.email_hour = int(email_hour)

    await db.commit()
    await db.refresh(row)
    return _effective(row)


def as_dict(row: Optional[UserSettings]) -> Dict[str, Any]:
    """调试/测试用的行快照（无行时为 None）"""
    if row is None:
        return {}
    return {
        "daily_review_limit": int(row.daily_review_limit),
        "email_hour": int(row.email_hour),
    }
