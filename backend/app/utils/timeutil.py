"""
时区工具（业务日界统一为 Asia/Shanghai，见 docs/decisions.md#F-32）

统一"今日"日界计算：业务日界按 Asia/Shanghai（北京时间 00:00），
存储/比较使用 UTC。旧代码在多个模块各自 `now.replace(hour=0,...)`（UTC 零点），
导致北京 08:00 前的答题/统计计入前一日。

本模块同时提供**到期时刻对齐**（阶段 3.7）：把 `next_review_at` 锚定到
业务时区的某个整点，理由见 `align_to_hour_of_day` 与 `business_day_index`。
"""

from datetime import datetime, timezone, timedelta

# 业务日界时区（可与配置联动；当前固定为上海时间）
_DAY_BOUNDARY_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")

#: 对外暴露的业务时区
#:
#: 项目**没有**用户级时区字段（`User` 表里没有），所有"哪一天"的判断
#: 都锚在这个时区上（日界、提醒、到期时刻）。把到期时刻锚在同一个时区是
#: 必须的：否则"日界在 +08:00、到期时刻在 UTC"会让同一张卡在两种口径下
#: 属于不同的日子。用户级时区是已知的未完成项。
BUSINESS_TZ = _DAY_BOUNDARY_TZ


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
    return _as_utc(dt).astimezone(BUSINESS_TZ).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).astimezone(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    """无时区时按 UTC 处理（SQLite 不存时区，历史行取出来可能是 naive 的）"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def business_day_index(dt: datetime) -> int:
    """时刻落在**业务日**的第几天（自公元 1 年 1 月 1 日起算）

    ## 为什么需要"按天"而不是"按小时差"

    两个时刻相差多少天，有两种算法，而它们在业务上不等价：

        连续差： (t2 - t1).total_seconds() / 86400     → 1.71 天
        业务日差： business_day_index(t2) - business_day_index(t1)  → 2

    调度需要的是**后者**。原因：用户说的"隔了一天"指的是日历上的下一天，
    不是 24 小时。晚上 23:00 复习、第二天早上 08:00 再看到这张卡，
    连续差只有 0.375 天，但用户与调度器的共识都是"过了一天"。

    这个区别在本项目里不是审美问题：FSRS 用 `elapsed < 1` 判断"同日复习"
    并据此走短时公式（见 `fsrs_service.stability_short_term`）。
    若用连续差，**同一个学习时段内的两次复习**会因为相差几十分钟而被
    判成"不同日"，或者反过来，隔夜复习（20 小时）被判成"同日" ——
    两种都会让记忆状态更新走错分支。
    """
    return _as_utc(dt).astimezone(BUSINESS_TZ).date().toordinal()


def days_between_business_days(start: datetime, end: datetime) -> int:
    """两个时刻相隔几个**业务日**（end 早于 start 时为负）"""
    return business_day_index(end) - business_day_index(start)


def align_to_hour_of_day(
    dt: datetime,
    hour: int,
    *,
    not_before: datetime | None = None,
) -> datetime:
    """把时刻对齐到业务时区当天的 `hour:00`（**向下**取整，返回 UTC）

    ## 为什么到期时刻要对齐

    改造前 `next_review_at = now + timedelta(days=interval)`，到期时刻等于
    "上次复习的钟点"。后果是**到期时刻会漂移**：今晚 23:40 复习的卡，
    下次就在 23:40 到期，再下次还在 23:40 —— 用户被要求在凌晨刷新页面。

    更实际的问题是它会让卡片**漏掉一整天**：用户习惯早上 08:00 复习，
    而卡片在 09:00 到期，于是今天看不到它、明天才出现，间隔凭空多了一天。
    把到期时刻锚到每天固定的整点（默认凌晨 4 点，与 Anki 的 rollover
    hour 同源）之后，"今天该不该复习"与用户的日历一致。

    ## 为什么向下取整而不是向上

    向上取整（"下一个整点"）会把间隔系统性拉长近一天：用户晚上复习、
    到期时刻是凌晨，向上取整必然落到后天。向下取整让跨度落在
    `(interval - 1, interval]` 天内，与"间隔 N 天"的直觉一致。

    向下取整在旧设计里是危险的（可能让 `elapsed < 1` 而误入同日分支），
    但 `business_day_index` 已把 elapsed 改成**按业务日计数**，
    这个危险不复存在 —— 两处改动是一组，不能只做其中一处。

    Args:
        dt: 待对齐的时刻（naive 按 UTC）
        hour: 目标整点 0-23
        not_before: 若给定时，结果**必须严格晚于**它；否则顺延一天。
            这是"间隔 ≥ 1 天"的最后一道保险：卡片不得在复习的同一瞬间
            再次到期（那会形成"到期 → 复习 → 仍到期"的死循环）。

    Returns:
        UTC 时区的对齐结果
    """
    hour = max(0, min(23, int(hour)))
    local = _as_utc(dt).astimezone(BUSINESS_TZ)
    aligned = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if aligned > local:
        aligned -= timedelta(days=1)
    if not_before is not None and aligned.astimezone(timezone.utc) <= _as_utc(not_before):
        aligned += timedelta(days=1)
    return aligned.astimezone(timezone.utc)

