"""
复习状态服务（overhaul-plan 阶段 3.1 / 3.12）

把"学习调度状态"的读写从 `QuizItem` 上收敛到 `ReviewState`，并在此过程中
保持**双向兼容**（渐进迁移，可回退）：

    ┌─ 读取 ──────────────────────────────────────────────┐
    │ get_state() 先查 review_states；查不到则从 quiz_items │
    │ 的旧字段**惰性补建**一条（老数据无需一次性迁移完）    │
    └─────────────────────────────────────────────────────┘

    ┌─ 写入 ──────────────────────────────────────────────┐
    │ apply_sm2_result() 同时更新 review_states 与          │
    │ quiz_items 的旧字段（双写）。任一时刻回滚代码，        │
    │ 旧字段都还是正确的值。                                │
    └─────────────────────────────────────────────────────┘

这样设计的原因：阶段 3 的目标是**最终**删掉 `quiz_items` 上的调度字段，
但那需要到期队列、统计、掌握度、前端全部切换。一次性切换风险过大，
而双写 + 惰性迁移允许分批推进，且每批都可回退。

## 为什么 item_type 必须参与键

`card_id` 与 `quiz_id` 都是 UUID 字符串，语义上无法区分。同一张卡片
既是"卡片"（整体回忆）又是"题目的宿主"（具体问法），记忆强度不同。
所以键是 `(user_id, item_type, item_id)`，唯一约束也含 `item_type`。
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.knowledge_card import KnowledgeCard
from ..models.quiz_item import QuizItem
from ..models.review_state import (
    ITEM_TYPE_CARD,
    ITEM_TYPE_QUIZ,
    ReviewState,
    ReviewStateKind,
)
from .scheduler_service import (
    ALGORITHM_SM2,
    PASS_QUALITY,
    ScheduleOutcome,
    derive_kind,
    sm2_kind,
)

logger = logging.getLogger(__name__)

#: 兼容别名：本模块内部（`_bootstrap_state`）与历史测试都按这个私有名引用
_derive_kind = derive_kind


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: Optional[datetime]) -> Optional[datetime]:
    """归一化时区（SQLite 不存时区，历史行可能是 naive）"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# 注：原本此处有一个本地 `_derive_kind`（由 repetition 与最近成绩推导学习阶段），
# 已上移到 `scheduler_service.derive_kind` 并在文件顶部以别名引入。
# 上移的原因：判断"这次复习后处于哪个阶段"是**调度**的职责，
# 而 SM-2 路径（`scheduler_service._advance_sm2`）也要用它；
# 留在这里会让 scheduler_service 反向依赖本模块，形成循环导入。


async def get_state(
    db: AsyncSession,
    user_id: str,
    item_type: str,
    item_id: str,
    *,
    create_from_legacy: bool = True,
) -> Optional[ReviewState]:
    """读取复习状态；缺失时从旧字段惰性补建

    Args:
        db: 数据库会话
        user_id: 用户 ID
        item_type: 'card' | 'quiz'
        item_id: 卡片或题目 ID
        create_from_legacy: 查不到时是否从 `quiz_items` 的旧字段补建。
            对 `item_type='quiz'` 默认补建（保证老数据可用）；
            `item_type='card'` 时旧字段里没有卡片级调度信息，补建的是
            "全新的、立即可复习"状态。

    Returns:
        ReviewState 或 None（题目不存在/不属于该用户时为 None）
    """
    state = (await db.execute(
        select(ReviewState).where(
            ReviewState.user_id == user_id,
            ReviewState.item_type == item_type,
            ReviewState.item_id == item_id,
        )
    )).scalars().first()
    if state is not None:
        return state
    if not create_from_legacy:
        return None

    return await _bootstrap_state(db, user_id, item_type, item_id)


async def _bootstrap_state(
    db: AsyncSession, user_id: str, item_type: str, item_id: str,
) -> Optional[ReviewState]:
    """从旧字段补建一条 ReviewState

    - `item_type='quiz'`：复制 `quiz_items` 上的 interval/repetition/EF/
      next_review_at/last_reviewed_at（**这是老数据不丢进度的关键**）
    - `item_type='card'`：旧字段里没有卡片级信息，建一条全新状态
      （interval=1, repetition=0, EF=2.5, next_review_at=None 表示立即可复习）
    """
    if item_type == ITEM_TYPE_QUIZ:
        quiz = (await db.execute(
            select(QuizItem).where(
                QuizItem.id == item_id, QuizItem.user_id == user_id,
            )
        )).scalars().first()
        if quiz is None:
            return None
        state = ReviewState(
            user_id=user_id,
            item_type=item_type,
            item_id=item_id,
            interval_days=int(quiz.interval or 1),
            repetition=int(quiz.repetition or 0),
            easiness_factor=float(quiz.easiness_factor or 2.5),
            next_review_at=_as_aware(quiz.next_review_at),
            last_reviewed_at=_as_aware(quiz.last_reviewed_at),
            review_count=int(quiz.review_count or 0),
            lapses=0,
            state=_derive_kind(int(quiz.repetition or 0), None),
        )
    elif item_type == ITEM_TYPE_CARD:
        card = (await db.execute(
            select(KnowledgeCard.id).where(
                KnowledgeCard.id == item_id, KnowledgeCard.user_id == user_id,
            )
        )).scalar()
        if card is None:
            return None
        state = ReviewState(
            user_id=user_id,
            item_type=item_type,
            item_id=item_id,
            interval_days=1,
            repetition=0,
            easiness_factor=2.5,
            next_review_at=None,
            last_reviewed_at=None,
            review_count=0,
            lapses=0,
            state=ReviewStateKind.new,
        )
    else:
        logger.warning("未知的 item_type，拒绝创建复习状态: %s", item_type)
        return None

    db.add(state)
    await db.commit()
    await db.refresh(state)
    logger.debug("已补建复习状态: %s:%s", item_type, item_id[:8])
    return state


def _apply_result_to_state(
    state: ReviewState, quality: int, next_review_at: datetime, now: datetime,
) -> None:
    """把调度结果里"与算法无关"的部分写进 ReviewState

    只负责 last_reviewed_at / next_review_at / review_count / lapses；
    **间隔、难度与学习阶段由调用方写入**（两个算法写的东西不同，
    阶段的推导规则也不同 —— 见 `scheduler_service._advance_sm2` 的说明）。
    """
    state.last_reviewed_at = now
    state.next_review_at = next_review_at
    state.review_count = (state.review_count or 0) + 1
    if quality < PASS_QUALITY:
        state.lapses = (state.lapses or 0) + 1


async def apply_schedule_result(
    db: AsyncSession,
    user_id: str,
    item_type: str,
    item_id: str,
    *,
    outcome: ScheduleOutcome,
    quality: int,
    now: Optional[datetime] = None,
) -> Optional[ReviewState]:
    """把一个 `ScheduleOutcome` 落库（`review_states` + `quiz_items` 旧字段双写）

    ## 为什么落库只认 `ScheduleOutcome`，不认具体算法

    落库要做的事（写哪些列、旧字段怎么镜像、lapses 怎么加）**与算法无关**。
    把算法判断留在 `scheduler_service.advance` 里、让这里只消费统一口径，
    换算法时才不会出现"新路径写了 stability、旧路径忘了写"的漏列。

    ## stability / difficulty 在 SM-2 路径下被清空（不是保留旧值）

    这两个字段的语义是"**当前**由 FSRS 维护的记忆状态"。回退到 SM-2 之后，
    SM-2 改了 `interval` 却不会改 S —— 那么原来的 S 就不再对应这张卡的
    真实状态了。留着它有两种坏结果：

    - 之后切回 FSRS，`schedule()` 会拿这个**过期**的 S 当输入，
      而它和 SM-2 刚写下的 interval 互相矛盾（S 的定义就是 interval 在
      R=90% 时的值）；
    - NULL 与"有值"在 `schedule()` 里走的是**两条不同分支**
      （是否要用 interval/EF 接管），留一个过期的值会走错分支。

    清空之后语义重新变得干净：NULL ⟺ 当前不由 FSRS 调度。

    Args:
        outcome: `scheduler_service.advance` 的结果
        quality: 本次评分 0-5（决定 lapses 与是否算成功）
        now: 复习时间

    Returns:
        更新后的 ReviewState；题目不存在时为 None
    """
    now = now or _now()
    state = await get_state(db, user_id, item_type, item_id)
    if state is None:
        return None

    state.interval_days = int(outcome.interval_days)
    state.repetition = int(outcome.repetition)
    state.easiness_factor = float(outcome.easiness_factor)
    if outcome.algorithm == ALGORITHM_SM2:
        state.stability = None
        state.difficulty = None
    else:
        state.stability = outcome.stability
        state.difficulty = outcome.difficulty

    _apply_result_to_state(state, quality, _as_aware(outcome.next_review_at) or now, now)
    # 学习阶段以调度器给的结果为准：两个算法对"这次复习后处于哪个阶段"
    # 的推导规则不同（FSRS 还有 SM-2 推不出的情况，例如"新卡 + Easy
    # 直接进长期复习"），在这里统一落库可以避免每个算法各写一遍。
    state.state = outcome.state

    # 双写旧字段（仅题目维度有旧字段）
    #
    # ⚠️ 这里**不递增** `quiz.review_count`：调用方
    # （`review_service.submit_answer`）已经在同一事务里做过这件事。
    # 本函数的职责是把"已经算好的结果"同步过来，不是重新计算；
    # 两边各加一次会让一次作答计成两次（本轮实测：review_count 变成 2）。
    if item_type == ITEM_TYPE_QUIZ:
        quiz = (await db.execute(
            select(QuizItem).where(
                QuizItem.id == item_id, QuizItem.user_id == user_id,
            )
        )).scalars().first()
        if quiz is not None:
            quiz.interval = int(outcome.interval_days)
            quiz.repetition = int(outcome.repetition)
            quiz.easiness_factor = float(outcome.easiness_factor)
            quiz.next_review_at = _as_aware(outcome.next_review_at)
            quiz.last_reviewed_at = now
            # review_count 与 review_states 对齐（以调用方写入的值为准）
            state.review_count = int(quiz.review_count or state.review_count or 0)

    return state


async def apply_sm2_result(
    db: AsyncSession,
    user_id: str,
    item_type: str,
    item_id: str,
    *,
    quality: int,
    interval_days: int,
    repetition: int,
    easiness_factor: float,
    next_review_at: datetime,
    now: Optional[datetime] = None,
) -> Optional[ReviewState]:
    """把已算好的 SM-2 结果落库（`apply_schedule_result` 的兼容包装）

    ⚠️ 生产路径已经统一走 `scheduler_service.advance` +
    `apply_schedule_result`，本函数保留是为了：
    1. 迁移脚本与既有测试仍按这个签名调用；
    2. 显式表达"这是一次 SM-2 落库" —— 它会按 `ALGORITHM_SM2` 处理，
       即**清空** stability/difficulty（见 `apply_schedule_result` 的说明）。

    新代码不要再用它：它要求调用方自己算 SM-2，而"每个调用点各算一次"
    正是阶段 3.6 要消除的分叉来源。
    """
    outcome = ScheduleOutcome(
        interval_days=int(interval_days),
        repetition=int(repetition),
        easiness_factor=float(easiness_factor),
        next_review_at=next_review_at,
        # 用推导出的阶段而不是调用方传入的，保证与旧行为一致
        state=sm2_kind(int(repetition), quality >= PASS_QUALITY),
        algorithm=ALGORITHM_SM2,
    )
    return await apply_schedule_result(
        db, user_id, item_type, item_id,
        outcome=outcome, quality=quality, now=now,
    )


async def list_due_states(
    db: AsyncSession,
    user_id: str,
    *,
    item_type: Optional[str] = None,
    now: Optional[datetime] = None,
    limit: int = 50,
) -> list[ReviewState]:
    """列出到期的复习状态（按到期时间升序，最过期的优先）

    注意：只返回**已存在于 review_states 的行**。尚未惰性补建的老数据
    不在其中 —— 调用方若要覆盖老数据，应先跑
    `scripts/migrate_review_states.py`。
    """
    now = now or _now()
    query = (
        select(ReviewState)
        .where(
            ReviewState.user_id == user_id,
            (ReviewState.next_review_at.is_(None)) | (ReviewState.next_review_at <= now),
        )
        .order_by(ReviewState.next_review_at.asc().nulls_first())
        .limit(limit)
    )
    if item_type:
        query = query.where(ReviewState.item_type == item_type)
    return list((await db.execute(query)).scalars().all())


async def count_states(db: AsyncSession, user_id: Optional[str] = None) -> dict[str, int]:
    """统计各 item_type 的状态条数（迁移脚本与自检用）"""
    query = select(ReviewState.item_type, func.count()).group_by(ReviewState.item_type)
    if user_id:
        query = query.where(ReviewState.user_id == user_id)
    rows = (await db.execute(query)).all()
    return {item_type: count for item_type, count in rows}


async def count_due_cards(db: AsyncSession, user_id: str) -> int:
    """统计当前到期可复习的**卡片**数（阶段 3.12 的入口指标）"""
    now = _now()
    return (await db.execute(
        select(func.count()).select_from(ReviewState).where(
            ReviewState.user_id == user_id,
            ReviewState.item_type == ITEM_TYPE_CARD,
            (ReviewState.next_review_at.is_(None)) | (ReviewState.next_review_at <= now),
        )
    )).scalar() or 0


__all__ = [
    "get_state",
    "apply_schedule_result",
    "apply_sm2_result",
    "list_due_states",
    "count_states",
    "count_due_cards",
    "PASS_QUALITY",
    "ITEM_TYPE_CARD",
    "ITEM_TYPE_QUIZ",
]
