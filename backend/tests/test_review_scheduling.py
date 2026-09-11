"""阶段 3.7：到期时刻的调度策略（间隔抖动 + 学习时段对齐）

## 这一层和 3.6 的区别

3.6 换的是**算法**（记忆状态怎么更新）；3.7 换的是**策略**
（把"间隔 N 天"变成"哪一天的几点到期"）。两者必须分开测：

- 算法错了，记忆模型就是错的，且不可事后纠正；
- 策略错了，用户会在错误的日子看到卡片。

而策略还必须**与算法无关** —— "同批卡片挤在同一天"和"到期时刻漂到凌晨"
是 SM-2 时期就存在的老问题（overhaul-plan §2.4 L-4 第 4、5 条），
不是换算法带来的，所以 `review_scheduler='sm2'` 回退路径同样要施加。

## 三条必须成立的不变量

1. **间隔 ≥ 1 天**：否则卡片会"到期 → 复习 → 仍到期"死循环；
2. **到期时刻严格晚于本次复习**：同上，且这是 `not_before` 守卫的职责；
3. **抖动不改变记忆状态**：S/D 是模型，到期日是政策。抖动把卡片摊开，
   不能让模型以为这张卡变难或变易了。

## elapsed 为什么要按业务日算

这是本轮最容易被忽略、后果却最直接的一处：

    晚上 23:00 复习 → 次日早上 08:00 再看到它
    连续差：0.375 天   → 被判成"同日复习" → 走短时公式
    业务日差：1 天     → 正确

FSRS 用 `elapsed < 1` 区分"同日"与"隔日"，而"同日"是日历概念。
用连续小时差会让**同一个学习时段内的两次复习**（隔几十分钟）和
**隔夜复习**（20 小时）得到相反的判定。见 `test_elapsed_*`。
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.knowledge_card import CardType, KnowledgeCard
from app.models.note import Note, SourceType
from app.models.quiz_item import QuestionType, QuizItem
from app.models.review_state import (
    ITEM_TYPE_CARD,
    ReviewState,
    ReviewStateKind,
)
from app.models.user import User
from app.services import fsrs_service as F
from app.services import review_service, scheduler_service
from app.services.scheduler_service import (
    ALGORITHM_FSRS,
    ALGORITHM_SM2,
)
from app.utils.timeutil import (
    BUSINESS_TZ,
    align_to_hour_of_day,
    business_day_index,
    days_between_business_days,
)

#: 周一 09:00 北京（= 01:00 UTC）。用北京时间来读测试里的所有时刻，
#: 因为被对齐的正是业务时区。
MON_0900 = datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc)
#: 周一 23:00 北京
MON_2300 = datetime(2026, 9, 14, 15, 0, tzinfo=timezone.utc)
#: 周二 08:00 北京
TUE_0800 = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)


def local(dt: datetime) -> datetime:
    """转成业务时区，便于按人话断言（"周二 04:00"）"""
    return dt.astimezone(BUSINESS_TZ)


def settings(*, fuzz: float = 0.0, due_hour: int = 4, algorithm: str = ALGORITHM_FSRS):
    from types import SimpleNamespace

    return SimpleNamespace(
        review_scheduler=algorithm,
        review_fuzz_ratio=fuzz,
        review_due_hour=due_hour,
        fsrs_request_retention=F.DEFAULT_REQUEST_RETENTION,
        fsrs_max_interval_days=F.MAX_INTERVAL_DAYS,
    )


def review_state(*, interval_days: int = 100, elapsed_days=None, **kw) -> ReviewState:
    """构造一条复习前状态

    默认让 `elapsed == interval_days` 且 `stability == interval_days`：
    这样复习前的 R 恰为 0.9（S 的定义点），测试不必再推算 R。
    需要别的经过时间时传 `elapsed_days`。
    """
    elapsed = interval_days if elapsed_days is None else elapsed_days
    base = dict(
        user_id="u", item_type=ITEM_TYPE_CARD, item_id="c",
        interval_days=interval_days, repetition=3, easiness_factor=2.5,
        state=ReviewStateKind.review,
        stability=float(interval_days), difficulty=5.0,
        last_reviewed_at=MON_0900 - timedelta(days=elapsed),
    )
    base.update(kw)
    return ReviewState(**base)


# ---------------------------------------------------------------------------
# 业务日算术
# ---------------------------------------------------------------------------

class TestBusinessDayArithmetic:
    """elapsed 必须按**业务日**计数，不能按小时差"""

    def test_same_business_day(self):
        assert business_day_index(MON_0900) == business_day_index(MON_2300)
        assert days_between_business_days(MON_0900, MON_2300) == 0

    def test_overnight_review_counts_as_one_day(self):
        """★ 本轮最要紧的一条

        23:00 → 次日 08:00 连续差只有 0.375 天，业务日差是 1。
        用连续差会让这次复习走 FSRS 的"同日"分支（短时公式），
        于是隔夜复习不更新长期记忆强度。
        """
        assert (TUE_0800 - MON_2300).total_seconds() / 86400 == pytest.approx(0.375)
        assert days_between_business_days(MON_2300, TUE_0800) == 1

    def test_boundary_is_business_midnight(self):
        """业务日界是北京 00:00，不是 UTC 00:00"""
        before = datetime(2026, 9, 14, 15, 59, tzinfo=timezone.utc)  # 周一 23:59
        after = datetime(2026, 9, 14, 16, 1, tzinfo=timezone.utc)    # 周二 00:01
        assert days_between_business_days(before, after) == 1

    def test_naive_treated_as_utc(self):
        naive = datetime(2026, 9, 14, 1, 0)
        assert business_day_index(naive) == business_day_index(MON_0900)

    def test_scheduler_elapsed_is_whole_days(self):
        elapsed = scheduler_service.elapsed_business_days(MON_2300, TUE_0800)
        assert elapsed == 1 and isinstance(elapsed, int)


# ---------------------------------------------------------------------------
# 到期时刻对齐
# ---------------------------------------------------------------------------

class TestAlignToHourOfDay:
    """把到期时刻锚到业务时区的固定整点"""

    def test_floor_to_same_local_day(self):
        """周一 09:00 + 6 天 → 周日 04:00（**向下**取整）

        向下取整让跨度落在 (interval-1, interval] 天内，与"间隔 6 天"的
        直觉一致；向上取整会系统性拉长近一天（用户晚上复习、到期时刻在凌晨，
        向上取整必然落到后天）。
        """
        target = align_to_hour_of_day(MON_0900 + timedelta(days=6), 4)
        assert target == datetime(2026, 9, 20, 4, 0, tzinfo=BUSINESS_TZ)
        assert target.tzinfo == timezone.utc

    def test_floor_rolls_back_when_hour_not_reached(self):
        """当天该整点还没到 → 取前一天（这才是"向下"取整）"""
        early = datetime(2026, 9, 14, 18, 0, tzinfo=timezone.utc)  # 周二 02:00
        assert align_to_hour_of_day(early, 4) == datetime(
            2026, 9, 14, 4, 0, tzinfo=BUSINESS_TZ
        )

    def test_exact_hour_is_idempotent(self):
        at_hour = datetime(2026, 9, 14, 4, 0, tzinfo=BUSINESS_TZ)
        assert align_to_hour_of_day(at_hour, 4) == at_hour

    def test_not_before_guard_pushes_one_day(self):
        """★ 不变量 2：结果必须**严格晚于** not_before

        没有这道守卫时，间隔 1 天 + 对齐会算出"今天 04:00"，
        而那是过去 —— 卡片立刻再次到期，与"复习完就到期"的死循环等价。
        """
        result = align_to_hour_of_day(MON_0900, 4, not_before=MON_0900)
        assert result > MON_0900
        assert result == datetime(2026, 9, 15, 4, 0, tzinfo=BUSINESS_TZ)

    def test_hour_is_clamped(self):
        assert align_to_hour_of_day(MON_0900, 25) == align_to_hour_of_day(MON_0900, 23)
        assert align_to_hour_of_day(MON_0900, -7) == align_to_hour_of_day(MON_0900, 0)


# ---------------------------------------------------------------------------
# 抖动
# ---------------------------------------------------------------------------

class TestFuzzInterval:
    """间隔抖动：把同批卡片摊开，而不改变平均间隔"""

    def test_below_minimum_is_untouched(self):
        """间隔 < 3 天不抖动

        1 天粒度下最小抖动是 ±1 天 = ±33%，那不是摊负载而是改写学习节奏；
        而且短间隔本来也不会雪崩 —— 它们全都该到期。
        """
        for interval in (1, 2):
            for u in (0.0, 0.25, 0.5, 0.9, 0.999):
                assert F.fuzz_interval(interval, rand=u) == interval

    def test_offset_range_and_extremes(self):
        """rand 的端点给出 ±delta，中间给出 0"""
        assert F.fuzz_interval(100, rand=0.0) == 95      # delta = 5
        assert F.fuzz_interval(100, rand=0.999999) == 105
        assert F.fuzz_interval(100, rand=0.5) == 100

    def test_minimum_delta_is_one_day(self):
        """只按比例算会让短间隔的抖动变成 0（3 天的 5% = 0.15 → 0），
        而"同批卡片全挤在 3 天后"恰恰是最常见的雪崩形态"""
        assert F.fuzz_interval(3, rand=0.0) == 2
        assert F.fuzz_interval(3, rand=0.999999) == 4
        assert F.fuzz_interval(10, rand=0.0) == 9        # round(0.5) = 0 → 仍取 1

    def test_never_below_one_day(self):
        """★ 不变量 1：抖动不得把间隔压到 0（会形成到期死循环）"""
        for interval in range(1, 40):
            for u in (0.0, 0.1, 0.5, 0.9, 0.999999):
                assert F.fuzz_interval(interval, rand=u) >= 1

    def test_respects_max_interval(self):
        assert F.fuzz_interval(3650, rand=0.999999, max_interval_days=3650) == 3650
        assert F.fuzz_interval(100, rand=0.999999, max_interval_days=100) == 100

    def test_ratio_zero_disables(self):
        for u in (0.0, 0.3, 0.999999):
            assert F.fuzz_interval(100, rand=u, ratio=0.0) == 100

    def test_nan_rand_does_not_propagate(self):
        assert F.fuzz_interval(100, rand=float("nan")) == 95

    def test_actually_spreads_a_batch(self):
        """抖动的**目的**：同一间隔的一批卡片必须落在多个不同的日子上"""
        # 间隔 20 → delta = max(1, round(1.0)) = 1 → 恰好三档 {19,20,21}
        short = {F.fuzz_interval(20, rand=i / 40) for i in range(40)}
        assert short == {19, 20, 21}
        # 长间隔摊得更开：200 → delta = 10 → 21 档
        long = {F.fuzz_interval(200, rand=i / 40) for i in range(40)}
        assert len(long) > 10, f"长间隔没有摊开：{sorted(long)}"
        assert all(190 <= v <= 210 for v in long)


# ---------------------------------------------------------------------------
# 策略与算法的分离
# ---------------------------------------------------------------------------

class TestSchedulingPolicy:
    """`apply_scheduling_policy`：抖动 + 对齐，且与算法无关"""

    def test_due_hour_is_applied(self):
        outcome = scheduler_service.advance(
            review_state(), 4, method="self_rating", now=MON_0900,
            settings=settings(due_hour=4), rand=0.5,
        )
        assert local(outcome.next_review_at).hour == 4
        assert local(outcome.next_review_at).minute == 0

    def test_due_at_is_strictly_after_now(self):
        """★ 不变量 2（端到端）：任何配置下都不得算出过去/此刻的到期时间"""
        for interval in (1, 2, 3, 6, 100):
            for now in (MON_0900, MON_2300):
                st = review_state(interval_days=interval, state=ReviewStateKind.learning)
                outcome = scheduler_service.advance(
                    st, 4, method="self_rating", now=now,
                    settings=settings(due_hour=4), rand=0.0,
                )
                assert outcome.next_review_at > now

    def test_fuzz_does_not_touch_memory_state(self):
        """★ 不变量 3：抖动是**调度政策**，不得改变 S/D/R

        若抖动被误加到 S 上，同一张卡在两次相同评分下会得到不同的记忆状态，
        而记忆状态是不可事后纠正的（见 3.6 的说明）。
        """
        kwargs = dict(
            state=review_state(interval_days=30, last_reviewed_at=MON_0900 - timedelta(days=30)),
            quality=4, method="self_rating", now=MON_0900,
        )
        a = scheduler_service.advance(**kwargs, settings=settings(fuzz=0.0), rand=0.0)
        b = scheduler_service.advance(**kwargs, settings=settings(fuzz=0.20), rand=0.0)
        assert a.interval_days != b.interval_days, "本用例需要抖动确实生效"
        assert (a.stability, a.difficulty, a.predicted_retention) == (
            b.stability, b.difficulty, b.predicted_retention,
        )

    def test_due_hour_negative_disables_alignment(self):
        """负值 = 关闭对齐（保留"上次复习的钟点"这一旧行为）"""
        outcome = scheduler_service.advance(
            review_state(interval_days=6), 4, method="self_rating", now=MON_0900,
            settings=settings(due_hour=-1), rand=0.5,
        )
        assert outcome.next_review_at == MON_0900 + timedelta(days=outcome.interval_days)
        assert local(outcome.next_review_at).hour == local(MON_0900).hour

    def test_due_window_never_exceeds_the_interval(self):
        """★ 对齐只挪动"当天的几点"，不改变"几天后"

        到期时刻落在 `(now + interval - 1 天, now + interval 天]` 内 ——
        向下取整最多提前不到一天，且永远不会早于本次复习。
        """
        for interval in (1, 2, 3, 6, 20, 100):
            outcome = scheduler_service.advance(
                review_state(interval_days=interval), 4, method="self_rating",
                now=MON_0900, settings=settings(due_hour=4), rand=0.5,
            )
            span = outcome.next_review_at - MON_0900
            assert timedelta(days=outcome.interval_days - 1) < span
            assert span <= timedelta(days=outcome.interval_days)

    def test_fuzz_applies_to_sm2_fallback_too(self):
        """同批卡片挤在同一天是 SM-2 时期就有的问题，回退路径同样要抖"""
        st = review_state(interval_days=100, repetition=3)
        fuzzed = {
            scheduler_service.advance(
                st, 4, method="self_rating", now=MON_0900, rand=i / 20,
                algorithm=ALGORITHM_SM2,
                settings=settings(fuzz=0.05, algorithm=ALGORITHM_SM2),
            ).interval_days
            for i in range(20)
        }
        assert len(fuzzed) > 1, f"SM-2 回退路径没有施加抖动：{fuzzed}"

    def test_policy_is_applied_after_the_algorithm(self):
        """顺序：先抖间隔，再对齐到期时刻

        反过来先对齐再抖动，抖动会把时刻重新拉回随机钟点，对齐白做。
        """
        base = scheduler_service.advance(
            review_state(interval_days=30), 4, method="self_rating", now=MON_0900,
            settings=settings(fuzz=0.0), rand=0.5,
        ).interval_days
        outcome = scheduler_service.advance(
            review_state(interval_days=30), 4, method="self_rating", now=MON_0900,
            settings=settings(fuzz=0.10), rand=0.0,
        )
        delta = max(1, round(base * 0.10))
        assert outcome.interval_days == base - delta
        assert local(outcome.next_review_at).hour == 4, "抖动之后到期时刻被拨离了整点"


class TestAvalancheAcceptance:
    """3.7 的验收点：同一批卡片不再挤在同一天到期"""

    def test_batch_of_identical_cards_spreads_across_days(self):
        """★ 30 张初始状态完全相同的卡片（同一次导入的典型形态）

        改造前它们**永远**同一天到期（同一个 S → 同一个间隔 → 同一个
        `next_review_at`），用户会在"0 张"与"30 张"之间反复横跳。
        """
        import random

        rng = random.Random(20260911)
        due_days = {
            local(scheduler_service.advance(
                review_state(interval_days=60), 4, method="self_rating", now=MON_0900,
                settings=settings(fuzz=0.05), rand=rng.random(),
            ).next_review_at).date()
            for _ in range(30)
        }
        assert len(due_days) >= 3, f"30 张同批卡片只落在 {len(due_days)} 天：{sorted(due_days)}"

    def test_batch_is_still_centered_on_the_algorithm_interval(self):
        """摊开之后**平均**间隔仍等于算法给出的间隔

        抖动改变的是"哪一天"，不是"多久之后" —— 否则复习总量会整体变化，
        而"同等保持率下复习量下降"是 3.6 的验收前提，不能被抖动偷走。
        """
        import random

        base = scheduler_service.advance(
            review_state(interval_days=60), 4, method="self_rating", now=MON_0900,
            settings=settings(fuzz=0.0), rand=0.5,
        ).interval_days
        delta = max(1, round(base * 0.05))

        rng = random.Random(7)
        values = [
            scheduler_service.advance(
                review_state(interval_days=60), 4, method="self_rating", now=MON_0900,
                settings=settings(fuzz=0.05), rand=rng.random(),
            ).interval_days
            for _ in range(200)
        ]
        mean = sum(values) / len(values)
        assert abs(mean - base) <= 1, f"抖动把平均间隔从 {base} 挪到了 {mean}"
        assert min(values) == base - delta
        assert max(values) == base + delta


# ---------------------------------------------------------------------------
# 端到端
# ---------------------------------------------------------------------------

async def _make_user(session_factory) -> str:
    uid = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(User(
            id=uid, email=f"{uid[:8]}@example.com", username=f"u{uid[:8]}",
            hashed_password="x", is_active=True,
        ))
        await db.commit()
    return uid


async def _make_card(session_factory, user_id: str) -> tuple[str, str]:
    note_id = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(Note(id=note_id, user_id=user_id, title="t", source_type=SourceType.pdf))
        await db.commit()
    card_id = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(KnowledgeCard(
            id=card_id, user_id=user_id, note_id=note_id,
            card_type=CardType.concept, title="机器学习", content="让计算机从数据中学习",
        ))
        await db.commit()
    return card_id, note_id


async def _make_quiz(session_factory, user_id: str, card_id: str, note_id: str) -> str:
    quiz_id = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(QuizItem(
            id=quiz_id, user_id=user_id, note_id=note_id, card_id=card_id,
            question="什么是机器学习", answer="机器学习",
            question_type=QuestionType.short_answer,
            interval=1, repetition=0, easiness_factor=2.5, next_review_at=None,
        ))
        await db.commit()
    return quiz_id


@pytest.mark.asyncio
class TestEndToEnd:
    """落库的 `next_review_at` 必须真的带着策略的痕迹"""

    async def test_submitted_review_lands_on_the_due_hour(self, test_db):
        uid = await _make_user(test_db)
        card_id, note_id = await _make_card(test_db, uid)
        quiz_id = await _make_quiz(test_db, uid, card_id, note_id)

        async with test_db() as db:
            await review_service.submit_answer(
                quiz_id, uid, "机器学习", 0, db, self_rating=4,
            )

        async with test_db() as db:
            quiz = (await db.execute(
                select(QuizItem).where(QuizItem.id == quiz_id)
            )).scalars().first()
        assert quiz.next_review_at is not None
        assert local(quiz.next_review_at).hour == 4, (
            f"到期时刻没有对齐到整点：{local(quiz.next_review_at)}"
        )
        assert quiz.next_review_at > quiz.last_reviewed_at

    async def test_old_fields_mirror_the_aligned_time(self, test_db):
        """旧字段是权威调度的镜像 —— 对齐也必须镜像过去，
        否则到期队列（仍读 quiz_items）与 review_states 会给出两个答案"""
        uid = await _make_user(test_db)
        card_id, note_id = await _make_card(test_db, uid)
        quiz_id = await _make_quiz(test_db, uid, card_id, note_id)

        async with test_db() as db:
            await review_service.submit_answer(
                quiz_id, uid, "机器学习", 0, db, self_rating=4,
            )

        from app.models.review_state import ITEM_TYPE_QUIZ, ReviewState as RS

        async with test_db() as db:
            state = (await db.execute(
                select(RS).where(RS.item_type == ITEM_TYPE_QUIZ, RS.item_id == quiz_id)
            )).scalars().first()
            quiz = (await db.execute(
                select(QuizItem).where(QuizItem.id == quiz_id)
            )).scalars().first()
        assert quiz.next_review_at.replace(tzinfo=None) == state.next_review_at.replace(tzinfo=None)
        assert quiz.interval == state.interval_days
