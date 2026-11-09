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

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models.user_settings import UserSettings

logger = logging.getLogger(__name__)


def _clamp(value, low, high, name: str):
    """把环境默认值夹到 API/DB 都接受的范围，并把越界写进日志。"""
    clamped = min(high, max(low, value))
    if clamped != value:
        logger.warning(
            "环境配置 %s=%r 超出设置页范围 [%r, %r]，运行时按 %r 处理",
            name,
            value,
            low,
            high,
            clamped,
        )
    return clamped


def deployment_defaults() -> Dict[str, Any]:
    """环境配置里的默认值（设置页未覆盖时使用）

    `config.py` 是部署面，可能被写成 4 道/天、0.5 留存率这类越界值；
    响应模型有范围校验，直接透传会让 `GET /api/settings` 变成 500。
    在唯一的出口处夹紧，用户行永远不会被写成非法值。
    """
    settings = get_settings()
    return {
        "daily_review_limit": _clamp(
            int(settings.daily_review_limit), 5, 100, "DAILY_REVIEW_LIMIT"
        ),
        "email_hour": _clamp(
            int(settings.email_reminder_hour), 0, 23, "EMAIL_REMINDER_HOUR"
        ),
        "desired_retention": _clamp(
            float(settings.fsrs_request_retention),
            0.70,
            0.98,
            "FSRS_REQUEST_RETENTION",
        ),
        "max_interval_days": _clamp(
            int(settings.fsrs_max_interval_days),
            365,
            3650,
            "FSRS_MAX_INTERVAL_DAYS",
        ),
        "output_locale": "follow",
        "card_max_new_per_run": _clamp(
            int(settings.card_max_new_per_run),
            50,
            1000,
            "CARD_MAX_NEW_PER_RUN",
        ),
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


def _apply_overrides(
    row: UserSettings,
    *,
    daily_review_limit: Optional[int],
    email_hour: Optional[int],
    desired_retention: Optional[float],
    max_interval_days: Optional[int],
    output_locale: Optional[str],
    card_max_new_per_run: Optional[int],
    clean_simplify_formula: Optional[bool],
    clean_normalize_spacing: Optional[bool],
    clean_normalize_heading_levels: Optional[bool],
) -> None:
    """把显式传入的 PATCH 字段写到行上；None = 不改。"""
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

    ## 并发首次建行

    两个请求可能同时 `SELECT` 到 None，然后都 INSERT；后提交的会撞
    `uq_user_settings_user_id`。这里不把 IntegrityError 直接甩给 API
    （那会变成 500），而是回滚后重取一次，把本次覆盖应用到已存在的行。
    """
    kwargs = {
        "daily_review_limit": daily_review_limit,
        "email_hour": email_hour,
        "desired_retention": desired_retention,
        "max_interval_days": max_interval_days,
        "output_locale": output_locale,
        "card_max_new_per_run": card_max_new_per_run,
        "clean_simplify_formula": clean_simplify_formula,
        "clean_normalize_spacing": clean_normalize_spacing,
        "clean_normalize_heading_levels": clean_normalize_heading_levels,
    }

    for attempt in range(2):
        row = await get_settings_row(db, user_id)
        if row is None:
            row = UserSettings(
                user_id=user_id,
                **deployment_defaults(),
            )
            db.add(row)

        _apply_overrides(row, **kwargs)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            if attempt == 1:
                raise
            # 另一个请求赢了 INSERT：重取它建的行，再应用一次本次覆盖。
            continue

        await db.refresh(row)
        return _effective(row)

    raise RuntimeError("unreachable: settings update retry loop exhausted")



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
