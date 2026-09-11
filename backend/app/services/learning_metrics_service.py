"""
学习度量服务（overhaul-plan 阶段 3.14）

回答用户唯一真正关心的问题：**「我到底记住了没有？」**

## 四类度量

| 度量 | 含义 | 数据来源 |
|---|---|---|
| **保持率曲线** | 复习后经过 t 天，还能回忆起来的比例 | 同一学习项的相邻两次复习（前次为通过、后次是否通过） |
| **校准曲线** | 自评说"想起来了"，实际是否真的想起来 | 四档自评 vs 后续实际表现 |
| **遗忘分布** | 哪些卡片反复忘（leech 候选） | 同一项的连续失败次数 |
| **复习负载** | 未来 1/7/30 天各有多少项到期 | `review_states.next_review_at` |

## 为什么每个度量都必须带"样本是否充足"的门槛

现场实测（`scripts/_audit_metrics_data.py`，2026-09-11）：

    review_logs 共 194 条，全部 grading_method='legacy'，self_rating 全为 NULL
    191 道题中 188 道只复习过 1 次；仅有的 3 组重复复习间隔均为 0 天
    单用户

在这份数据上：

- **保持率曲线**需要"间隔 > 0 的相邻复习对"，实测**一对都没有**。
  强行出图只会得到一条由 3 个 0 天间隔点拼出的假曲线。
- **校准曲线**需要自评与实际表现的配对，实测**零样本**。

因此本模块的契约是：**数据不足时明确返回 `insufficient_data=true`
与真实样本量，而不是返回一条看起来像结论的曲线**。
一个"用 3 个样本画出的 87% 保持率"比不显示更糟 —— 用户会据此判断
自己的记忆状况。

门槛取 20（每次度量的最小样本）。这个值不是统计意义上的显著样本，
而是"少于 20 个点画出来的曲线形状完全由噪声决定"的经验下限；
它足够低，让真实使用一两周后就能越过。
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.review_log import ReviewLog
from ..models.review_state import ReviewState

logger = logging.getLogger(__name__)

#: 每次度量的最小样本量；低于它返回 insufficient_data
MIN_SAMPLE = 20

#: SM-2 成功阈值（quality >= 3），与 sm2_service / review_state_service 一致
PASS_QUALITY = 3

#: 保持率曲线的天数分桶边界（左闭右开），最后一段为 >= 该值
RETENTION_BUCKETS: tuple[int, ...] = (1, 3, 7, 14, 30, 60)

#: 校准曲线的质量分档（四档自评映射到 quality 0/3/4/5）
CALIBRATION_TIERS: tuple[int, ...] = (0, 3, 4, 5)

#: 判定为 leech 的连续失败次数（阶段 3.8 的阈值）
LEECH_LAPSE_THRESHOLD = 8

#: 保持率曲线的最小有效间隔（天）
#:
#: 为什么不是 `gap_days > 0`：用户答错后立刻重做一次，间隔可能是 5 分钟
#: （0.0035 天），它**经过了时间但不足以检验记忆**。用 `> 0` 会把这类
#: 同次会话内的重做算成"保持率证据"，从而系统性地高估保持率
#: （刚看完答案当然答得对）。
#: 取半天：低于它的间隔一律不算，宁可少算也不虚报。
MIN_RETENTION_GAP_DAYS = 0.5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: Optional[datetime]) -> Optional[datetime]:
    """归一化时区（SQLite 不存时区，历史行读出来是 naive）

    不做这一步会抛 `can't subtract offset-naive and offset-aware datetimes`，
    而这只在"库里只有老数据"时出现 —— 恰恰是本项目当前的常态。
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# 纯函数层（可直接单测，不碰数据库）
# ---------------------------------------------------------------------------

def build_review_pairs(
    records: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把同一学习项的复习记录按时间排序，配对成"前次 → 后次"

    Args:
        records: 每条含 `item_key`（区分学习项）、`review_at`、`quality`

    Returns:
        每项含 `item_key`、`gap_days`（两次间隔天数）、`prev_passed`、
        `passed`（后次是否通过）的列表。

    设计要点：
    - **只配对相邻两次**。用"首次 vs 末次"会跨越中间的全部复习，
      得到的间隔没有对应的记忆强度含义。
    - 间隔为 0 的对（同一天内重复提交）**仍然返回**，由调用方决定是否
      计入保持率 —— 它们对"保持率"没有意义（没经过时间），
      但对"是否答对"仍有信息。本模块在 `compute_retention_curve` 中排除它们。
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        key = record.get("item_key")
        if key is None:
            continue
        when = _as_aware(record.get("review_at"))
        if when is None:
            continue
        grouped.setdefault(key, []).append({
            "review_at": when,
            "quality": int(record.get("quality") or 0),
        })

    pairs: list[dict[str, Any]] = []
    for key, items in grouped.items():
        items.sort(key=lambda r: r["review_at"])
        for previous, current in zip(items, items[1:], strict=False):
            gap_days = (current["review_at"] - previous["review_at"]).total_seconds() / 86400.0
            pairs.append({
                "item_key": key,
                "gap_days": gap_days,
                "prev_passed": previous["quality"] >= PASS_QUALITY,
                "passed": current["quality"] >= PASS_QUALITY,
                # 预测的回忆概率：用前一次复习时的间隔近似
                "predicted": None,
            })
    return pairs


def compute_retention_curve(pairs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """按天数分桶计算保持率

    只统计 `prev_passed=True` 且 `gap_days >= MIN_RETENTION_GAP_DAYS` 的对：

    - 前次没通过时谈不上"保持"（本来就没记住）
    - 间隔过短时不足以检验记忆（实测库里 3 组重复复习全是同次会话内的
      重做，间隔接近 0 天，正是这个原因让保持率曲线无数据）

    Returns:
        {"buckets": [...], "sample_size": n, "insufficient_data": bool,
         "min_sample": MIN_SAMPLE}
    """
    usable = [
        p for p in pairs
        if p["prev_passed"] and p["gap_days"] >= MIN_RETENTION_GAP_DAYS
    ]

    buckets: list[dict[str, Any]] = []
    boundaries = list(RETENTION_BUCKETS)
    for index, lower in enumerate(boundaries):
        upper = boundaries[index + 1] if index + 1 < len(boundaries) else None
        in_bucket = [
            p for p in usable
            if p["gap_days"] >= lower and (upper is None or p["gap_days"] < upper)
        ]
        if not in_bucket:
            continue
        passed = sum(1 for p in in_bucket if p["passed"])
        buckets.append({
            "label": f"{lower}-{upper}天" if upper else f"{lower}天以上",
            "lower_days": lower,
            "upper_days": upper,
            "total": len(in_bucket),
            "passed": passed,
            "retention": round(passed / len(in_bucket) * 100, 1),
        })

    return {
        "buckets": buckets,
        "sample_size": len(usable),
        "insufficient_data": len(usable) < MIN_SAMPLE,
        "min_sample": MIN_SAMPLE,
    }


def compute_calibration_curve(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """按自评分档统计实际通过率（校准曲线）

    只统计**同时有自评与实际判分**的记录。自评本身即是 quality 的来源时，
    "预测值与实际值"是同一个数，没有校准含义 —— 因此本函数统计的是
    "自评为某档时，后续复习的实际通过率"，需要同一学习项有后续记录。

    Args:
        records: 每条含 `item_key`、`review_at`、`quality`、`self_rating`

    Returns:
        {"tiers": [...], "sample_size": n, "insufficient_data": bool}
    """
    # 按学习项聚合，找出"自评过、且有后续复习"的配对
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        key = record.get("item_key")
        if key is None:
            continue
        when = _as_aware(record.get("review_at"))
        if when is None:
            continue
        grouped.setdefault(key, []).append({
            "review_at": when,
            "quality": int(record.get("quality") or 0),
            "self_rating": record.get("self_rating"),
        })

    samples: list[tuple[int, bool]] = []
    for items in grouped.values():
        items.sort(key=lambda r: r["review_at"])
        for current, following in zip(items, items[1:], strict=False):
            if current["self_rating"] is None:
                continue
            samples.append((
                int(current["self_rating"]),
                following["quality"] >= PASS_QUALITY,
            ))

    tiers: list[dict[str, Any]] = []
    for tier in CALIBRATION_TIERS:
        in_tier = [s for s in samples if s[0] == tier]
        if not in_tier:
            continue
        actual = sum(1 for _, passed in in_tier if passed)
        tiers.append({
            "self_rating": tier,
            "predicted_accuracy": round(tier / 5 * 100, 1),
            "actual_accuracy": round(actual / len(in_tier) * 100, 1),
            "total": len(in_tier),
            "passed": actual,
        })

    return {
        "tiers": tiers,
        "sample_size": len(samples),
        "insufficient_data": len(samples) < MIN_SAMPLE,
        "min_sample": MIN_SAMPLE,
    }


def compute_lapse_distribution(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """统计每个学习项的**当前连续失败次数**（用于 leech 检测）

    注意是"当前连续"而不是"累计"：一张卡以前错过很多次但最近连续答对，
    不应该继续被标为顽固卡。用累计值会让 leech 列表只增不减，
    用户很快就对它免疫。

    Returns:
        {"leech_candidates": [...], "max_consecutive_lapses": n, "items": n}
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        key = record.get("item_key")
        if key is None:
            continue
        when = _as_aware(record.get("review_at"))
        if when is None:
            continue
        grouped.setdefault(key, []).append({
            "review_at": when,
            "quality": int(record.get("quality") or 0),
        })

    candidates: list[dict[str, Any]] = []
    max_streak = 0
    for key, items in grouped.items():
        items.sort(key=lambda r: r["review_at"])
        streak = 0
        for item in items:
            if item["quality"] >= PASS_QUALITY:
                streak = 0
            else:
                streak += 1
        max_streak = max(max_streak, streak)
        if streak >= LEECH_LAPSE_THRESHOLD:
            candidates.append({
                "item_key": key,
                "consecutive_lapses": streak,
                "total_reviews": len(items),
            })

    candidates.sort(key=lambda c: c["consecutive_lapses"], reverse=True)
    return {
        "leech_candidates": candidates,
        "max_consecutive_lapses": max_streak,
        "items": len(grouped),
        "threshold": LEECH_LAPSE_THRESHOLD,
    }


def compute_review_load(
    due_dates: Iterable[Optional[datetime]], now: Optional[datetime] = None,
) -> dict[str, Any]:
    """统计未来各时间窗的到期数量

    `None` 表示"立即可复习"（与 review_state_service 的判定一致），
    计入 `due_now`。
    """
    now = now or _now()
    windows = {"due_now": 0, "next_24h": 0, "next_7d": 0, "next_30d": 0, "beyond_30d": 0}
    for raw in due_dates:
        when = _as_aware(raw)
        if when is None:
            windows["due_now"] += 1
            continue
        delta_days = (when - now).total_seconds() / 86400.0
        if delta_days <= 0:
            windows["due_now"] += 1
        elif delta_days <= 1:
            windows["next_24h"] += 1
        elif delta_days <= 7:
            windows["next_7d"] += 1
        elif delta_days <= 30:
            windows["next_30d"] += 1
        else:
            windows["beyond_30d"] += 1
    return windows


def summarize_forecast(
    due_dates: Sequence[Optional[datetime]], now: Optional[datetime] = None,
) -> dict[str, Any]:
    """未来 30 天**逐日**到期量（用于负载图）

    Returns:
        {"daily": [{"date": "YYYY-MM-DD", "count": n}, ...], "total": n}
    """
    now = now or _now()
    today = now.date()
    counter: dict[str, int] = {}
    overdue = 0
    beyond = 0

    for raw in due_dates:
        when = _as_aware(raw)
        if when is None:
            overdue += 1
            continue
        day = when.date()
        offset = (day - today).days
        if offset <= 0:
            overdue += 1
        elif offset > 30:
            beyond += 1
        else:
            key = day.isoformat()
            counter[key] = counter.get(key, 0) + 1

    daily = [
        {"date": (today + timedelta(days=offset)).isoformat(),
         "count": counter.get((today + timedelta(days=offset)).isoformat(), 0)}
        for offset in range(1, 31)
    ]
    return {"daily": daily, "overdue": overdue, "beyond_30d": beyond}


# ---------------------------------------------------------------------------
# 数据库层
# ---------------------------------------------------------------------------

async def _load_user_review_records(
    db: AsyncSession, user_id: str, item_type: Optional[str] = None,
) -> list[dict[str, Any]]:
    """读取该用户的复习事件流

    `item_key` 的构造规则（与 `ReviewState` 的键语义一致）：

    - 有 `card_id` → `card:<card_id>` —— **优先用它**
    - 否则有 `quiz_id` → `quiz:<quiz_id>`（`card_id` 是阶段 3.2 才加的列，
      历史行可能为空）
    - 两者都无 → 整条跳过（无法归属，参与聚合只会污染统计）

    为什么以 `card_id` 优先：题目会被"重新理解"整批替换，`quiz_id` 指向的
    行消失后历史记录就断了；`card_id` 是稳定的归属键。同一张卡片的多道题
    也应聚成一个学习项 —— 用户记住的是"这个概念"，不是"某一道题"。
    """
    rows = (await db.execute(
        select(
            ReviewLog.quiz_id, ReviewLog.card_id, ReviewLog.quality,
            ReviewLog.self_rating, ReviewLog.review_at,
        )
        .where(ReviewLog.user_id == user_id)
        .order_by(ReviewLog.review_at.asc())
    )).all()

    records: list[dict[str, Any]] = []
    for quiz_id, card_id, quality, self_rating, review_at in rows:
        if card_id:
            item_key = f"card:{card_id}"
        elif quiz_id:
            item_key = f"quiz:{quiz_id}"
        else:
            # 既无卡片也无题目：无法归属。静默丢弃是有意的 ——
            # 把这类记录混进"同一学习项"的配对会凭空造出不存在的间隔。
            continue
        records.append({
            "item_key": item_key,
            "quiz_id": quiz_id,
            "card_id": card_id,
            "quality": quality,
            "self_rating": self_rating,
            "review_at": review_at,
        })
    return records


async def get_learning_metrics(
    db: AsyncSession, user_id: str,
) -> dict[str, Any]:
    """汇总该用户的全部学习度量

    Returns:
        含 `retention` / `calibration` / `lapses` / `load` / `forecast` /
        `data_quality` 的字典。

        `data_quality` 是**刻意的设计**：它如实报告本度量的数据来源与
        已知局限，让前端能显示"为什么这里没有曲线"，而不是给用户
        一个空图或假图。
    """
    records = await _load_user_review_records(db, user_id)
    pairs = build_review_pairs(records)

    retention = compute_retention_curve(pairs)
    calibration = compute_calibration_curve(records)
    lapses = compute_lapse_distribution(records)

    state_rows = (await db.execute(
        select(ReviewState.item_type, ReviewState.next_review_at)
        .where(ReviewState.user_id == user_id)
    )).all()
    due_dates = [next_review_at for _item_type, next_review_at in state_rows]
    load = compute_review_load(due_dates)
    forecast = summarize_forecast(due_dates)

    legacy_count = sum(1 for r in records if r["self_rating"] is None)
    card_level = sum(1 for r in records if r["quiz_id"] is None)

    return {
        "retention": retention,
        "calibration": calibration,
        "lapses": lapses,
        "load": load,
        "forecast": forecast,
        "data_quality": {
            "total_reviews": len(records),
            "distinct_items": len({r["item_key"] for r in records}),
            "review_pairs": len(pairs),
            "pairs_with_time_gap": sum(
                1 for p in pairs if p["gap_days"] >= MIN_RETENTION_GAP_DAYS
            ),
            "self_rated_reviews": len(records) - legacy_count,
            "card_level_reviews": card_level,
            "tracked_items": len(state_rows),
            "min_sample": MIN_SAMPLE,
            "notes": _data_quality_notes(
                total=len(records),
                pairs_with_gap=sum(
                    1 for p in pairs if p["gap_days"] >= MIN_RETENTION_GAP_DAYS
                ),
                self_rated=len(records) - legacy_count,
                card_level=card_level,
            ),
        },
    }


def _data_quality_notes(
    *, total: int, pairs_with_gap: int, self_rated: int, card_level: int,
) -> list[str]:
    """生成"为什么没有曲线"的可读说明

    这些文案直接面向用户，因此必须说清**缺什么**与**怎么能有**，
    而不是只说"数据不足"。用户看到"再复习几次就会出现曲线"才知道
    这个功能不是坏的。
    """
    notes: list[str] = []
    if total == 0:
        notes.append("还没有任何复习记录。完成几次复习后，这里会显示你的记忆曲线。")
        return notes
    if pairs_with_gap < MIN_SAMPLE:
        notes.append(
            f"保持率曲线需要「同一内容间隔一天以上复习两次」的记录，"
            f"目前只有 {pairs_with_gap} 条（需 {MIN_SAMPLE} 条）。"
            "按复习计划正常复习几天后即可看到。"
        )
    if self_rated < MIN_SAMPLE:
        notes.append(
            f"校准曲线需要「自评 + 后续实际表现」配对，目前只有 {self_rated} 条"
            f"（需 {MIN_SAMPLE} 条）。自评功能需要你在答题后给出四档评分。"
        )
    if card_level and pairs_with_gap == 0:
        notes.append(
            f"其中 {card_level} 条是卡片级复习 —— 只要同一张卡片隔天再复习一次，"
            "就能开始形成保持率数据。"
        )
    return notes


__all__ = [
    "get_learning_metrics",
    "build_review_pairs",
    "compute_retention_curve",
    "compute_calibration_curve",
    "compute_lapse_distribution",
    "compute_review_load",
    "summarize_forecast",
    "MIN_SAMPLE",
    "MIN_RETENTION_GAP_DAYS",
    "PASS_QUALITY",
    "LEECH_LAPSE_THRESHOLD",
]
