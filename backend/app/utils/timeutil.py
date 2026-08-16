"""
时区工具（F-32 修复）

统一"今日"日界计算：业务日界按 Asia/Shanghai（北京时间 00:00），
存储/比较使用 UTC。旧代码在多个模块各自 `now.replace(hour=0,...)`（UTC 零点），
导致北京 08:00 前的答题/统计计入前一日。
"""

from datetime import datetime, timezone, timedelta

# 业务日界时区（可与配置联动；当前固定为上海时间）
_DAY_BOUNDARY_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def today_start_utc(now: datetime | None = None) -> datetime:
    """
    计算"今日"在业务日界（Asia/Shanghai 00:00）对应的 UTC 时刻

    Args:
        now: 当前时刻（UTC，缺省取 datetime.now(timezone.utc)）

    Returns:
        UTC 时区的今日零点（可直接用于 review_at >= ... 等比较）
    """
    if now is None:
        now = datetime.now(timezone.utc)
    return local_day_start_utc(now)


def local_day_start_utc(dt: datetime) -> datetime:
    """
    计算任意时刻在业务日界（Asia/Shanghai 00:00）对应的 UTC 时刻

    用于 7 天趋势等历史日期的日界统一。

    Args:
        dt: 任意时刻（无时区时按 UTC 处理）

    Returns:
        UTC 时区的该业务日零点
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(_DAY_BOUNDARY_TZ)
    local_midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(timezone.utc)
