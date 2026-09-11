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

## 当前公式（阶段 3.9 起）

    mastery = 成功次数比例 × 回忆概率 R × 100

    R = (1 + (19/81) · t/S) ** (-0.5)        ← FSRS 的幂律遗忘曲线

- `S`：**FSRS 的记忆强度**（`review_states.stability`）；
  行还没被 FSRS 调度过时按 `S := interval_days` 换算，
  这与调度器接管旧行用的是**同一条规则**（`fsrs_service.stability_or_interval`）
- `t`：距上次复习过了几个**业务日**（与调度器的 elapsed 口径一致）
- 该曲线满足 `R(S,S) = 0.9` —— 这正是 FSRS 调度器 `request_retention=0.9`
  的含义：**到期那一刻，模型认为你还有 90% 能想起来**

## ⚠️ 换成 FSRS 曲线后，掌握度整体**变高**了，这是对的

旧实现用指数曲线 `2^(-t/S)`，3 个月未复习（t = 9S）时 R ≈ 0.002；
FSRS 的幂律曲线给 **0.567**。差距不是实现误差，而是两条曲线的形状不同：
幂律的尾部厚得多，这正是 FSRS 敢于把间隔拉长的原因。

**真库实测（2026-09-11）把这件事推到了极端**：191 张有复习记录的卡片
`interval_days` **全部是 1**（SM-2 时期的间隔长期为 1，见附录 A 的
1058 行 `interval=1`），而最近一次复习在 80 天前。指数曲线给
`2^(-80) ≈ 8e-25` —— 于是**每一张卡的掌握度都被算成 0.0**，
字段仍然不携带任何信息。也就是说附录 G 的方向是对的（补上了时间维度），
但指数曲线的尾部太陡，在真实数据上退化成了常数 0。

换成幂律后 `R(80, S=1) = 0.225`，重算让 132 张卡拿到了 20-60 之间
可区分的分数（130 张成功率 1.0 + 2 张 0.5；另有 59 张最近 5 次全错，
掌握度**正确地**保持 0）。

**这不是放松了标准**，而是换成了与调度器同一条曲线 ——
否则"到期预测"与"掌握度"会在同一个界面上互相矛盾
（调度器说"90% 能想起来"，掌握度说"2%"），而 3.14 的校准曲线
（预测保持率 vs 实际正确率）会永远对不上。

## 边界

- 从未复习过 → 0（保持与旧行为一致，避免"没学过也有分"）
- `review_states` 里"复习过"的判据是 `last_reviewed_at IS NOT NULL`，
  **不再要求 `next_review_at` 非空** —— 后者是调度字段，删了排期不代表
  没复习过。旧实现要求它非空，会把"已复习但没有下次排期"的卡算成 0
- 只有旧字段可用的行（尚未补建 `review_states`）仍要求
  `next_review_at` 非空：那是 SM-2 时期判断"排过期"的唯一痕迹
- S/interval <= 0 或 NaN 时按 1 天处理（数值护栏在 `fsrs_service` 里）
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.knowledge_card import KnowledgeCard
from ..models.quiz_item import QuizItem
from ..models.review_log import ReviewLog
from ..models.review_state import ITEM_TYPE_CARD, ITEM_TYPE_QUIZ, ReviewState
from ..utils.timeutil import days_between_business_days
from . import fsrs_service

logger = logging.getLogger(__name__)

#: 成功判定阈值（SM-2 quality >= 3 为通过，与 sm2_service 一致）
PASS_QUALITY = 3

#: 参与成功率统计的最近记录条数
RECENT_WINDOW = 5


def compute_retrievability(
    elapsed_days: float,
    interval_days: float,
    stability: Optional[float] = None,
) -> float:
    """计算当前回忆概率（0-1）—— FSRS 的幂律遗忘曲线

        R(t, S) = (1 + (19/81) · t/S) ** (-0.5)

    性质与两个锚点：
      - `R(0, S) = 1`；`R(S, S) = 0.9`（S 的定义，也是调度器的目标保持率）
      - 单调递减，且比指数曲线**尾部厚得多**（t=9S 时仍有 0.567，
        而 `2^(-t/S)` 只给 0.002）—— 见模块说明

    Args:
        elapsed_days: 距上次复习的天数（<=0 按 0 处理，即 R=1）
        interval_days: 当前复习间隔（天），在 `stability` 缺失时充当 S
        stability: FSRS 的记忆强度；缺省时按 `S := interval_days` 换算

    Returns:
        float: 回忆概率，落在 (0, 1]

    ## 为什么保留 `interval_days` 参数而不是只收 S

    真库里 2241 行复习状态中，绝大多数还没有被 FSRS 调度过
    （`stability IS NULL`）。它们仍然必须显示一个掌握度，
    而换算规则与调度器接管旧行时用的**完全一致** —— 这样
    "调度器打算怎么排"和"界面显示还记得多少"从第一天起就不矛盾。
    """
    s = fsrs_service.stability_or_interval(stability, interval_days)
    return fsrs_service.retrievability(max(0.0, elapsed_days), s)


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
) -> Optional[tuple[Optional[float], float, datetime]]:
    """找出该卡片"最近一次复习"的记忆强度与时间

    查询合并两个来源，按时间取最新的一条：
      1. `review_states`（**权威**）：卡片自身（`item_type='card'`）
         与卡片下所有题目（`item_type='quiz'`）。它带着 FSRS 的
         `stability`，是阶段 3.9 之后掌握度的主输入。
      2. `quiz_items` 的旧字段（回退）：某些行可能还没被惰性补建出
         `review_states` 记录，此时至少还有 SM-2 留下的 interval。

    返回 `(stability, interval_days, last_reviewed_at)`；
    没有任何记录时返回 None。`stability` 为 None 表示那一行
    还没被 FSRS 调度过（由调用方按 `S := interval_days` 换算）。

    为什么需要两个来源：没有生成过题目的卡片在旧公式下恒为 0。
    允许直接复习卡片（阶段 3.12）之后，这类卡片也有了自己的记忆强度。
    """
    best: Optional[tuple[Optional[float], float, datetime]] = None

    def _consider(stability, interval_days, last_reviewed_at) -> None:
        nonlocal best
        anchor = _as_aware(last_reviewed_at)
        if anchor is None:
            return
        if best is None or anchor > best[2]:
            best = (stability, float(interval_days or 1), anchor)

    # 来源 1：review_states —— 卡片自身 + 卡片下题目
    #
    # `review_states` 一律以 `(user_id, item_type, item_id)` 为键，
    # 题目维度的行要以本卡片的 quiz_id 集合为条件查（item_id 是 quiz_id）。
    quiz_id_subq = select(QuizItem.id).where(
        QuizItem.card_id == card_id, QuizItem.user_id == user_id,
    )
    state_rows = await db.execute(
        select(
            ReviewState.stability,
            ReviewState.interval_days,
            ReviewState.last_reviewed_at,
        ).where(
            ReviewState.user_id == user_id,
            ReviewState.last_reviewed_at.is_not(None),
            or_(
                and_(
                    ReviewState.item_type == ITEM_TYPE_CARD,
                    ReviewState.item_id == card_id,
                ),
                and_(
                    ReviewState.item_type == ITEM_TYPE_QUIZ,
                    ReviewState.item_id.in_(quiz_id_subq),
                ),
            ),
        )
    )
    for stability, interval_days, last_reviewed_at in state_rows.all():
        _consider(stability, interval_days, last_reviewed_at)

    # 来源 2：quiz_items 旧字段（尚未补建 review_states 的行）
    quiz_rows = await db.execute(
        select(QuizItem.interval, QuizItem.next_review_at, QuizItem.last_reviewed_at)
        .where(QuizItem.card_id == card_id, QuizItem.user_id == user_id)
    )
    for interval, next_review_at, last_reviewed_at in quiz_rows.all():
        if next_review_at is None:
            # 历史数据没有下次复习时间 → 不参与（与旧行为一致）
            continue
        _consider(None, interval, last_reviewed_at)

    return best


async def _recent_quality_stats(
    card_id: str, user_id: str, db: AsyncSession,
) -> tuple[int, int]:
    """统计最近若干次复习的成功数与总数

    Returns:
        (success_count, total_count)；无记录时为 (0, 0)

    两个过滤条件都是必须的：

    - **按 user_id**：旧实现只按 quiz_id 过滤，一旦同一 quiz_id 出现在
      其他用户的记录里，正确率就被静默污染。
    - **题目 + 卡片两条来源**：旧实现只查 `quiz_id IN (卡片下的题)`,
      而卡片级复习（阶段 3.12）的 `quiz_id` 是 NULL，**永远不会命中** ——
      于是"没有题目的卡片"只能靠 `total == 0` 的兜底分支拿分，
      它的真实答题历史被完全忽略。`review_logs.card_id` 是阶段 3.2 加的
      稳定归属列（历史行已回填），用它才覆盖得全。
    """
    card_quiz_ids = select(QuizItem.id).where(
        QuizItem.card_id == card_id, QuizItem.user_id == user_id,
    )
    result = await db.execute(
        select(ReviewLog.quality)
        .where(
            ReviewLog.user_id == user_id,
            or_(
                ReviewLog.card_id == card_id,
                ReviewLog.quiz_id.in_(card_quiz_ids),
            ),
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

    stability, interval_days, last_reviewed_at = anchor
    # 按**业务日**算经过时间，与调度器的 elapsed 口径一致（阶段 3.7）。
    # 掌握度是展示量，用连续小时差本可以更"精确"，但那会让它与
    # 调度器/校准曲线引用不同的经过时间，同一个界面上出现两个 R。
    elapsed_days = max(0, days_between_business_days(last_reviewed_at, _now()))
    retrievability = compute_retrievability(elapsed_days, interval_days, stability)

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
