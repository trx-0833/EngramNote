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


def deployment_defaults() -> Dict[str, Any]:
    """环境配置里的默认值（设置页未覆盖时使用）"""
    settings = get_settings()
    return {
        "daily_review_limit": int(settings.daily_review_limit),
        "email_hour": int(settings.email_reminder_hour),
        "desired_retention": float(settings.fsrs_request_retention),
        "max_interval_days": int(settings.fsrs_max_interval_days),
        "output_locale": "follow",
        "card_max_new_per_run": int(settings.card_max_new_per_run),
        "clean_simplify_formula": bool(settings.clean_simplify_formula),
        "clean_normalize_spacing": bool(settings.clean_normalize_spacing),
        "clean_normalize_heading_levels": bool(settings.clean_normalize_heading_levels),
    }


def _effective(row: Optional[UserSettings]) -> Dict[str, Any]:
    values = deployment_defaults()
    if row is not None:
        values.update(
            {
                "daily_review_limit": int(row.daily_review_limit),
                "email_hour": int(row.email_hour),
                "desired_retention": float(row.desired_retention),
                "max_interval_days": int(row.max_interval_days),
                "output_locale": str(row.output_locale),
                "card_max_new_per_run": int(row.card_max_new_per_run),
                "clean_simplify_formula": bool(row.clean_simplify_formula),
                "clean_normalize_spacing": bool(row.clean_normalize_spacing),
                "clean_normalize_heading_levels": bool(
                    row.clean_normalize_heading_levels
                ),
            }
        )
    return values


async def get_settings_row(
    db: AsyncSession, user_id: str
) -> Optional[UserSettings]:
    """取用户的设置覆盖行；没有则返回 None（调用方应使用默认值）"""
    result = await db.execute(
        select(UserSettings).where(UserSettings.user_id == user_id)
    )
    return result.scalars().first()


async def get_effective_settings(db: AsyncSession, user_id: str) -> Dict[str, Any]:
    """读取合并后的有效设置（只读，不写库）"""
    return _effective(await get_settings_row(db, user_id))


async def update_effective_settings(
    db: AsyncSession,
    user_id: str,
    *,
    daily_review_limit: Optional[int] = None,
    email_hour: Optional[int] = None,
    desired_retention: Optional[float] = None,
    max_interval_days: Optional[int] = None,
    output_locale: Optional[str] = None,
    card_max_new_per_run: Optional[int] = None,
    clean_simplify_formula: Optional[bool] = None,
    clean_normalize_spacing: Optional[bool] = None,
    clean_normalize_heading_levels: Optional[bool] = None,
) -> Dict[str, Any]:
    """部分更新；没有行时创建。返回更新后的完整有效值。

    只更新显式传入的字段（`None` = 不改），这样 PATCH 的语义与
    "缺省字段保持原值"一致。布尔值必须用 `is not None` 判断 ——
    用真值判断会让"显式关闭"被当成"没提交"。
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
    if desired_retention is not None:
        row.desired_retention = float(desired_retention)
    if max_interval_days is not None:
        row.max_interval_days = int(max_interval_days)
    if output_locale is not None:
        row.output_locale = str(output_locale)
    if card_max_new_per_run is not None:
        row.card_max_new_per_run = int(card_max_new_per_run)
    if clean_simplify_formula is not None:
        row.clean_simplify_formula = bool(clean_simplify_formula)
    if clean_normalize_spacing is not None:
        row.clean_normalize_spacing = bool(clean_normalize_spacing)
    if clean_normalize_heading_levels is not None:
        row.clean_normalize_heading_levels = bool(clean_normalize_heading_levels)

    await db.commit()
    await db.refresh(row)
    return _effective(row)


def as_dict(row: Optional[UserSettings]) -> Dict[str, Any]:
    """调试/测试用的行快照（无行时为 None）"""
    if row is None:
        return {}
    return _effective(row)


SUPPORTED_OUTPUT_LOCALES = {"zh-CN", "en"}


def resolve_output_locale(
    output_locale: Optional[str], user_locale: Optional[str]
) -> str:
    """设置页的 `follow` 语义：跟随界面语言；显式值优先。

    worker 没有请求上下文，只能靠这条纯函数把「用户设置 + 用户语言」
    解析成提示词语言；未知值回落到中文，与 `core/i18n.DEFAULT_LOCALE` 一致。
    """
    if output_locale in SUPPORTED_OUTPUT_LOCALES:
        return output_locale
    if user_locale in SUPPORTED_OUTPUT_LOCALES:
        return user_locale
    return "zh-CN"


def scheduling_settings(values: Dict[str, Any], base=None):
    """把用户覆盖项包成 `scheduler_service.advance(settings=...)` 可用的对象

    只覆盖两个字段；其余（算法、抖动、日界）仍来自部署配置。用 `copy`
    而不是改全局单例，避免 A 用户的设置泄漏到 B 用户的下一次调度。
    """
    from copy import copy

    if base is None:
        base = get_settings()
    view = copy(base)
    view.fsrs_request_retention = float(values["desired_retention"])
    view.fsrs_max_interval_days = int(values["max_interval_days"])
    return view
