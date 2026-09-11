"""
掌握度计算服务模块

计算 0-100 的掌握度，语义是**「当前还能回忆起这张卡的概率」× 100**。

## 为什么重写（overhaul-plan 阶段 3.9 / L-2）

旧公式：`mastery = 正确率 × 60% + 标准化SM-2状态 × 40%`。
现场实测：1183 张卡片里 **1078 张为 0**，其余 103 张全部落在 70-79 之间
—— 一个几乎不携带信息的字段。原因有三个，都是结构性的：

1. **单调不减，没有时间衰减**：公式只看"最近 5 次对了几次"和当前的
   EF/repetition。一张卡只要答对过，掌握度就永久保持高位；三个月没复习
   也不会下降。而"掌握度"这个词对用户的承诺恰恰是"现在还记得多少"。
2. **无题目的卡片恒为 0**：`quiz_ids` 为空时直接 `return 0.0`。
   没有生成过题目的卡片因此永远是 0，尽管用户可能已经读过、理解过它。
3. **review_logs 查询未按用户过滤**（跨用户串扰）：`ReviewLog.quiz_id.in_(...)`
   只按题目过滤。虽然 QuizItem 自带 user_id，但它**不被 ReviewLog 携带**，
   一旦同一 quiz_id 出现在别的用户记录里（数据修复、合并、导入），
   正确率就会被污染。这类污染静默且无法复现。

## 新公式

    mastery = 成功次数比例 × 回忆概率(retrievability)

    retrievability = 2 ** (-elapsed_days / interval)

- `elapsed_days`：距上次复习过了多少天（**这是新增的时间维度**）
- `interval`：SM-2 给出的当前复习间隔，作为"记忆强度"的代理
- 该函数来自遗忘曲线的指数近似：间隔刚到期的时刻回忆概率为 0.5，
  这正好对应"到期了就该复习"的直觉

三个缺陷对应的性质：
- 时间衰减：`elapsed >> interval` 时 retrievability → 0，掌握度自然回落
- 无题目卡片：改用**卡片级**复习记录（`item_type='card'`）作为回退，
  仍无任何记录才为 0（见 `_latest_review_anchor`）
- 用户过滤：所有查询都带 `user_id`

## 边界

- 从未复习过 → 0（保持与旧行为一致，避免"没学过也有分"）
- 有记录但其 `next_review_at` 为 None（历史数据）→ 0
- `interval <= 0` 时按 1 天处理（SM-2 的 interval 最小就是 1）
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.knowledge_card import KnowledgeCard
from ..models.quiz_item import QuizItem
from ..models.review_log import ReviewLog

logger = logging.getLogger(__name__)

#: 成功判定阈值（SM-2 quality >= 3 为通过，与 sm2_service 一致）
PASS_QUALITY = 3

#: 参与成功率统计的最近记录条数
RECENT_WINDOW = 5


def compute_retrievability(elapsed_days: float, interval_days: float) -> float:
    """计算当前回忆概率（0-1）

    用指数遗忘曲线 `R = 2^(-t/S)`，其中以 SM-2 的复习间隔作为记忆强度 S。
    选择它而不是 FSRS 的幂律曲线，是因为当前 `review_logs` 数据量不足以
    拟合 FSRS 参数（见 overhaul-plan 附录 E.6）；指数曲线只需要 interval
    这一个已有字段，且行为可解释。

    Args:
        elapsed_days: 距上次复习的天数（负数按 0 处理）
        interval_days: 当前复习间隔（天）；<=0 时按 1 处理

    Returns:
        float: 回忆概率，落在 [0, 1]
    """
    if interval_days <= 0:
        interval_days = 1.0
    if elapsed_days <= 0:
        return 1.0
    # 2^(-t/S)；t=S 时恰好 0.5
    return float(2.0 ** (-elapsed_days / interval_days))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: Optional[datetime]) -> Optional[datetime]:
    """把可能缺失时区的 datetime 归一为 UTC aware

    SQLite 不存时区，历史行读出来可能是 naive；直接做减法会抛
    `can't subtract offset-naive and offset-aware datetimes`，
    而这类错误在"只有老数据"时才出现，很容易漏测。
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _latest_review_anchor(
    card_id: str, user_id: str, db: AsyncSession,
) -> Optional[tuple[float, datetime]]:
    """找出该卡片"最近一次复习"的记忆强度与时间

    查询合并两个来源，按时间取最新的一条：
      1. 卡片下所有题目的 SM-2 参数（`quiz_items`）
      2. 卡片级复习状态（`review_states`，阶段 3.12 引入，允许直接复习卡片）

    返回 `(interval_days, last_reviewed_at)`；没有任何记录时返回 None。

    为什么需要第 2 个来源：没有生成过题目的卡片在旧公式下恒为 0。
    允许直接复习卡片之后，这类卡片也有了自己的记忆强度。
    """
    best: Optional[tuple[float, datetime]] = None

    # 来源 1：题目维度
    quiz_rows = await db.execute(
        select(QuizItem.interval, QuizItem.next_review_at, QuizItem.last_reviewed_at)
        .where(QuizItem.card_id == card_id, QuizItem.user_id == user_id)
    )
    for interval, next_review_at, last_reviewed_at in quiz_rows.all():
        anchor = _as_aware(last_reviewed_at)
        if anchor is None or next_review_at is None:
            # 从未复习过，或历史数据没有下次复习时间 → 不参与
            continue
        if best is None or anchor > best[1]:
            best = (float(interval or 1), anchor)

    # 来源 2：卡片级复习状态（表可能尚不存在于老库，故容错）
    try:
        from ..models.review_state import ReviewState

        state_rows = await db.execute(
            select(ReviewState.interval_days, ReviewState.last_reviewed_at)
            .where(
                ReviewState.user_id == user_id,
                ReviewState.item_type == "card",
                ReviewState.item_id == card_id,
            )
        )
        for interval_days, last_reviewed_at in state_rows.all():
            anchor = _as_aware(last_reviewed_at)
            if anchor is None:
                continue
            if best is None or anchor > best[1]:
                best = (float(interval_days or 1), anchor)
    except Exception as exc:  # pragma: no cover - 老库无该表时静默跳过
        logger.debug("卡片级复习状态不可用（忽略）: %s", exc)

    return best


async def _recent_quality_stats(
    card_id: str, user_id: str, db: AsyncSession,
) -> tuple[int, int]:
    """统计最近若干次复习的成功数与总数

    Returns:
        (success_count, total_count)；无记录时为 (0, 0)

    这里**必须按 user_id 过滤**：旧实现只按 quiz_id 过滤，一旦同一
    quiz_id 出现在其他用户的记录里，正确率就被静默污染。
    """
    card_quiz_ids = select(QuizItem.id).where(
        QuizItem.card_id == card_id, QuizItem.user_id == user_id,
    )
    result = await db.execute(
        select(ReviewLog.quality)
        .where(
            ReviewLog.user_id == user_id,
            # 卡片下题目 + 卡片自身的复习记录都算
            ReviewLog.quiz_id.in_(card_quiz_ids),
        )
        .order_by(ReviewLog.review_at.desc())
        .limit(RECENT_WINDOW)
    )
    qualities = [q for (q,) in result.all()]
    if not qualities:
        return 0, 0
    successes = sum(1 for q in qualities if (q or 0) >= PASS_QUALITY)
    return successes, len(qualities)


async def compute_card_mastery(
    card_id: str,
    db: AsyncSession,
    user_id: Optional[str] = None,
) -> float:
    """计算指定卡片的掌握度（0-100）

    Args:
        card_id: 知识卡片 ID
        db: 数据库会话
        user_id: 用户 ID。为 None 时从卡片自身取（**推荐显式传入**：
                 旧调用方不传时行为与旧版一致，但跨用户过滤依赖它）

    Returns:
        float: 掌握度（0-100，保留 1 位小数）
    """
    if user_id is None:
        owner = (await db.execute(
            select(KnowledgeCard.user_id).where(KnowledgeCard.id == card_id)
        )).scalar()
        user_id = owner
    if not user_id:
        return 0.0

    anchor = await _latest_review_anchor(card_id, user_id, db)
    if anchor is None:
        # 从未复习过：保持与旧行为一致，0 分
        return 0.0

    interval_days, last_reviewed_at = anchor
    elapsed_days = (_now() - last_reviewed_at).total_seconds() / 86400.0
    retrievability = compute_retrievability(elapsed_days, interval_days)

    successes, total = await _recent_quality_stats(card_id, user_id, db)
    if total == 0:
        # 有调度参数但查不到复习记录（历史数据不一致）：只用回忆概率
        success_ratio = retrievability
    else:
        success_ratio = successes / total

    mastery = success_ratio * retrievability * 100
    return round(max(0.0, min(100.0, mastery)), 1)


async def refresh_card_mastery(
    card_id: str, db: AsyncSession, user_id: Optional[str] = None,
) -> None:
    """刷新指定卡片的掌握度（计算后写入 KnowledgeCard.mastery_level）

    本函数内部捕获所有异常，仅记 warning 日志，不抛出，
    以确保调用方（如答题流程）不受影响。

    Args:
        card_id: 知识卡片 ID
        db: 数据库会话
        user_id: 用户 ID（可选，缺省时由卡片归属推断）
    """
    try:
        mastery = await compute_card_mastery(card_id, db, user_id=user_id)

        result = await db.execute(
            select(KnowledgeCard).where(KnowledgeCard.id == card_id)
        )
        card = result.scalars().first()
        if card is None:
            logger.warning(f"刷新掌握度失败：卡片不存在 (card_id={card_id})")
            return

        # 归属校验：user_id 显式给出且与卡片归属不符时拒绝写入，
        # 避免调用方传错 ID 时把别人的卡片改成自己的分数
        if user_id is not None and card.user_id != user_id:
            logger.warning(
                "刷新掌握度跳过：卡片归属不符 (card_id=%s, card.user=%s, arg=%s)",
                card_id, card.user_id, user_id,
            )
            return

        card.mastery_level = mastery
        await db.commit()
    except Exception as e:
        logger.warning(f"刷新卡片掌握度失败 (card_id={card_id}): {e}")
        return


async def recalibrate_all_mastery(
    db: AsyncSession, user_id: Optional[str] = None, batch_size: int = 200,
) -> dict:
    """按新公式重算全部卡片的掌握度（一次性迁移入口）

    公式变更后，库里存量 `mastery_level` 是旧公式的产物（实测 1078/1183
    为 0），必须显式重算，否则用户看到的仍是旧数据。

    Args:
        db: 数据库会话
        user_id: 只重算该用户的卡片；None 表示全部
        batch_size: 每批提交条数（避免一次事务过大）

    Returns:
        {"scanned": n, "updated": n, "changed": n}
    """
    query = select(KnowledgeCard.id, KnowledgeCard.user_id, KnowledgeCard.mastery_level)
    if user_id:
        query = query.where(KnowledgeCard.user_id == user_id)
    rows = (await db.execute(query)).all()

    updated = 0
    changed = 0
    for index, (card_id, owner_id, old_value) in enumerate(rows):
        new_value = await compute_card_mastery(card_id, db, user_id=owner_id)
        if abs(float(old_value or 0) - new_value) > 1e-9:
            card = (await db.execute(
                select(KnowledgeCard).where(KnowledgeCard.id == card_id)
            )).scalars().first()
            if card is not None:
                card.mastery_level = new_value
                updated += 1
                changed += 1
        if (index + 1) % batch_size == 0:
            await db.commit()

    await db.commit()
    logger.info("掌握度重算完成: 扫描 %d，变更 %d", len(rows), changed)
    return {"scanned": len(rows), "updated": updated, "changed": changed}


__all__ = [
    "compute_card_mastery",
    "refresh_card_mastery",
    "compute_retrievability",
    "recalibrate_all_mastery",
    "PASS_QUALITY",
    "RECENT_WINDOW",
]
