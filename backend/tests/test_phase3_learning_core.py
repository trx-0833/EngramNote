"""
阶段 3 核心测试：掌握度公式、ReviewState、卡片直接复习

## 覆盖的三件事

| 项 | 旧行为（缺陷） | 新契约 |
|---|---|---|
| **3.9 掌握度** | 单调不减、无时间衰减；无题目的卡片恒为 0；review_logs 未按用户过滤 | 随遗忘曲线衰减；卡片级状态可支撑；全部查询带 user_id |
| **3.1 ReviewState** | 调度状态与题目内容同表，重跑理解即丢学习历史 | 状态独立成表，按 `(user, item_type, item_id)` 唯一；旧字段双写保回退 |
| **3.12 卡片复习** | 没出过题的卡片永远无法复习 | 卡片可直接以四档自评复习 |

## 为什么重点测边界

这三项的价值都在"旧数据/极端时间"上体现：一张 3 个月没复习的卡片、
一张从未出过题的卡片、一条来自别人的复习记录。happy path 测试对它们
没有鉴别力。
"""

import math
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.knowledge_card import CardType, KnowledgeCard
from app.models.note import Note, SourceType
from app.models.quiz_item import QuestionType, QuizItem
from app.models.review_log import ReviewLog
from app.models.review_state import (
    ITEM_TYPE_CARD,
    ITEM_TYPE_QUIZ,
    ReviewState,
    ReviewStateKind,
)
from app.models.user import User
from app.services import mastery_service, review_state_service
from app.services.mastery_service import compute_retrievability

INTERVAL_DAYS = 10


# ---------------------------------------------------------------------------
# 纯函数层：遗忘曲线
# ---------------------------------------------------------------------------

class TestRetrievability:
    """回忆概率函数（掌握度的核心维度）

    ⚠️ 阶段 3.9（附录 Y）把曲线从**指数** `2^(-t/S)` 换成了 **FSRS 幂律**
    `(1 + (19/81)·t/S)^(-0.5)`，因此本类的具体数值全部变了。
    换的理由是"调度器与掌握度必须引用同一条曲线"：调度器按 FSRS 排期
    （到期那天 R=0.9），若掌握度用指数曲线，同一张卡会同时被判成
    "90% 能想起来"和"2%"，而 3.14 的校准曲线永远对不上。

    这里保留的是**性质**（单调、锚点、边界），而不是某个旧公式的数值。
    """

    def test_at_due_date_is_the_request_retention(self):
        """间隔刚到期的时刻，回忆概率恰为 0.9

        这不是随便定的：FSRS 对 S 的定义就是"R 降到 90% 所需的天数"，
        也正是调度器的目标保持率（`fsrs_request_retention`）。
        旧实现这里是 0.5（指数曲线的性质），换曲线时一并改掉了。
        """
        assert compute_retrievability(INTERVAL_DAYS, INTERVAL_DAYS) == pytest.approx(0.9)

    def test_immediately_after_review_is_one(self):
        assert compute_retrievability(0, INTERVAL_DAYS) == 1.0
        # 负数（时钟回拨）按 0 处理，不应产生 >1 的概率
        assert compute_retrievability(-5, INTERVAL_DAYS) == 1.0

    def test_decays_monotonically(self):
        """必须单调递减：时间越久越记不住

        旧公式在这里是**常数**（不看时间），这正是它无信息量的根源。
        """
        values = [compute_retrievability(d, INTERVAL_DAYS) for d in (0, 5, 10, 20, 40)]
        assert values == sorted(values, reverse=True)
        assert values[0] > values[-1]

    def test_long_absence_decays_but_not_to_zero(self):
        """三个月不复习必须显著衰减，但**不会归零** —— 这是幂律曲线的形状

        旧实现（指数）在 t=9S 时给 0.002，新实现给 0.567。差距不是误差：
        幂律尾部厚得多，这正是 FSRS 敢于拉长间隔的依据。
        掌握度整体因此"变高"了，见 mastery_service 模块说明。
        """
        r = compute_retrievability(90, INTERVAL_DAYS)
        assert r < 0.7, f"90 天未复习的回忆概率仍为 {r}，衰减不足"
        assert r > 0.4, f"90 天未复习就衰减到 {r}，这是指数曲线的形状，不是 FSRS"
        # 更久的缺席会继续往下走，不会停在 0.5 附近
        assert compute_retrievability(10000, INTERVAL_DAYS) < 0.1

    def test_longer_interval_decays_slower(self):
        """间隔越长（记忆越牢），同样天数后回忆概率越高"""
        short = compute_retrievability(10, 5)
        long = compute_retrievability(10, 50)
        assert long > short

    def test_explicit_stability_wins_over_interval(self):
        """给了 FSRS 的 S 就用它，不再拿 interval 当代理

        这是 3.9 的核心：`review_states.stability` 才是记忆强度，
        `interval_days` 只是"实际排了几天"（还含阶段 3.7 的抖动）。
        """
        assert compute_retrievability(90, 10, stability=90) == pytest.approx(0.9)
        assert compute_retrievability(90, 10, stability=10) == pytest.approx(0.567, abs=0.01)

    def test_non_positive_interval_is_guarded(self):
        """interval<=0 时不得除零或返回 NaN（S 按 1 天处理）"""
        guarded = compute_retrievability(5, 0)
        assert guarded == pytest.approx(compute_retrievability(5, 1))
        assert compute_retrievability(5, -3) == pytest.approx(guarded)
        assert not math.isnan(compute_retrievability(5, 0))
        assert not math.isnan(compute_retrievability(5, 10, stability=float("nan")))


# ---------------------------------------------------------------------------
# 测试数据
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
    """建一张卡片（含笔记），返回 (card_id, note_id)"""
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


async def _make_quiz(
    session_factory, user_id: str, card_id: str, note_id: str, *,
    interval: int = 1, repetition: int = 0, ef: float = 2.5,
    last_reviewed_days_ago: float | None = None,
    next_review_at: datetime | None = None,
) -> str:
    quiz_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    last = now - timedelta(days=last_reviewed_days_ago) if last_reviewed_days_ago is not None else None
    async with session_factory() as db:
        db.add(QuizItem(
            id=quiz_id, user_id=user_id, note_id=note_id, card_id=card_id,
            question="什么是机器学习", answer="机器学习",
            question_type=QuestionType.short_answer,
            interval=interval, repetition=repetition, easiness_factor=ef,
            last_reviewed_at=last, next_review_at=next_review_at,
        ))
        await db.commit()
    return quiz_id


async def _card_mastery(session_factory, card_id: str) -> float:
    async with session_factory() as db:
        return float((await db.execute(
            select(KnowledgeCard.mastery_level).where(KnowledgeCard.id == card_id)
        )).scalar() or 0.0)


# ---------------------------------------------------------------------------
# 3.9 掌握度
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestMasteryFormula:

    async def test_never_reviewed_is_zero(self, test_db):
        """从未复习过 → 0（保持与旧行为一致，避免"没学过也有分"）"""
        uid = await _make_user(test_db)
        card_id, _ = await _make_card(test_db, uid)
        async with test_db() as db:
            assert await mastery_service.compute_card_mastery(card_id, db, user_id=uid) == 0.0

    async def test_card_without_quiz_is_not_stuck_at_zero(self, test_db):
        """**核心回归**：没有题目的卡片只要有卡片级复习记录就该有分数

        旧公式在 `quiz_ids` 为空时直接 `return 0.0`，导致"没生成过题目的
        卡片永远显示 0 掌握度"。现场实测 1183 张卡片里 1078 张为 0，
        其中很大一部分就是这个原因。
        """
        uid = await _make_user(test_db)
        card_id, _ = await _make_card(test_db, uid)

        # 建卡片级状态并模拟"刚复习过、答得很好"
        async with test_db() as db:
            state = await review_state_service.get_state(db, uid, ITEM_TYPE_CARD, card_id)
            assert state is not None
            state.interval_days = 10
            state.repetition = 3
            state.last_reviewed_at = datetime.now(timezone.utc)
            await db.commit()

        async with test_db() as db:
            db.add(ReviewLog(
                user_id=uid, quiz_id=None, note_id=None,
                user_answer="想起来了", is_correct=True, quality=5,
                self_rating=5, grading_method="self_rating",
                time_spent_ms=500, review_at=datetime.now(timezone.utc),
            ))
            await db.commit()

        async with test_db() as db:
            mastery = await mastery_service.compute_card_mastery(card_id, db, user_id=uid)
        assert mastery > 0, (
            "没有题目的卡片仍然恒为 0 —— 卡片级复习记录未被采纳（阶段 3.12 失效）"
        )

    async def test_mastery_decays_over_time(self, test_db):
        """**核心验收**：同样答对，三个月未复习的卡片掌握度必须显著低于刚复习的

        ⚠️ 衰减**幅度**在阶段 3.9 换曲线后变小了：旧指数曲线给 0.002，
        FSRS 幂律给 0.567（见 `TestRetrievability` 的说明）。
        这里断言的是"显著更低"这个性质，不再是旧曲线的尾部数值 ——
        绑死一个只属于指数曲线的阈值，等于把实现细节写进验收标准。
        """
        uid = await _make_user(test_db)
        fresh_card, fresh_note = await _make_card(test_db, uid)
        stale_card, stale_note = await _make_card(test_db, uid)

        await _make_quiz(
            test_db, uid, fresh_card, fresh_note,
            interval=10, repetition=3, last_reviewed_days_ago=0,
            next_review_at=datetime.now(timezone.utc) + timedelta(days=10),
        )
        await _make_quiz(
            test_db, uid, stale_card, stale_note,
            interval=10, repetition=3, last_reviewed_days_ago=90,
            next_review_at=datetime.now(timezone.utc) - timedelta(days=80),
        )

        async with test_db() as db:
            fresh = await mastery_service.compute_card_mastery(fresh_card, db, user_id=uid)
            stale = await mastery_service.compute_card_mastery(stale_card, db, user_id=uid)

        assert fresh > stale, (
            f"3 个月未复习的卡片掌握度({stale}) 不低于刚复习的({fresh}) —— 缺少时间衰减"
        )
        assert stale < fresh * 0.7, (
            f"衰减幅度不足：fresh={fresh}, stale={stale}（期望 stale < 70% fresh）"
        )
        assert stale > 0, "掌握度不该因为没有复习就归零（那与'从未学过'无法区分）"

    async def test_stability_drives_mastery_not_interval(self, test_db):
        """★ 3.9 的核心：掌握度用的是 FSRS 的 S，不是 interval

        两张卡的 `interval_days` 相同，但 S 相差一个数量级 ——
        掌握度必须跟着 S 走。若不跟随，界面上显示的"还记得多少"
        就与调度器实际依据的记忆强度脱节。
        """
        uid = await _make_user(test_db)
        weak_card, weak_note = await _make_card(test_db, uid)
        strong_card, strong_note = await _make_card(test_db, uid)
        weak_quiz = await _make_quiz(test_db, uid, weak_card, weak_note, interval=30)
        strong_quiz = await _make_quiz(test_db, uid, strong_card, strong_note, interval=30)

        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
        async with test_db() as db:
            for quiz_id, stability in ((weak_quiz, 30.0), (strong_quiz, 300.0)):
                db.add(ReviewState(
                    user_id=uid, item_type=ITEM_TYPE_QUIZ, item_id=quiz_id,
                    interval_days=30, repetition=2, easiness_factor=2.5,
                    stability=stability, difficulty=5.0,
                    last_reviewed_at=thirty_days_ago, review_count=2,
                    state=ReviewStateKind.review,
                ))
            await db.commit()

        async with test_db() as db:
            weak = await mastery_service.compute_card_mastery(weak_card, db, user_id=uid)
            strong = await mastery_service.compute_card_mastery(strong_card, db, user_id=uid)

        assert strong > weak, (
            f"interval 相同但 S 不同，掌握度应当不同：weak={weak}, strong={strong}"
        )

    async def test_card_level_reviews_count_toward_success_ratio(self, test_db):
        """★ 卡片级复习必须计入成功率（旧查询永远命中不了它们）

        旧实现只查 `quiz_id IN (卡片下的题)`，而卡片级复习（阶段 3.12）
        的 `quiz_id` 是 NULL —— 永远不命中。于是"没有题目的卡片"只能靠
        `total == 0` 的兜底分支拿分，它的真实答题历史被完全忽略：
        一张卡片级复习全错的卡，与全对的卡得到同样的分数。
        """
        uid = await _make_user(test_db)
        good_card, _ = await _make_card(test_db, uid)
        bad_card, _ = await _make_card(test_db, uid)

        async with test_db() as db:
            for card_id, quality in ((good_card, 5), (bad_card, 1)):
                for _ in range(3):
                    db.add(ReviewLog(
                        user_id=uid, quiz_id=None, card_id=card_id,
                        note_id=None, user_answer="x", is_correct=quality >= 3,
                        quality=quality, self_rating=quality,
                        grading_method="self_rating", item_type="card",
                        time_spent_ms=100, review_at=datetime.now(timezone.utc),
                    ))
                # 两张卡的调度状态完全相同 → 差别只能来自成功率
                db.add(ReviewState(
                    user_id=uid, item_type=ITEM_TYPE_CARD, item_id=card_id,
                    interval_days=10, repetition=1, easiness_factor=2.5,
                    stability=10.0, difficulty=5.0,
                    last_reviewed_at=datetime.now(timezone.utc),
                    review_count=3, state=ReviewStateKind.review,
                ))
            await db.commit()

        async with test_db() as db:
            good = await mastery_service.compute_card_mastery(good_card, db, user_id=uid)
            bad = await mastery_service.compute_card_mastery(bad_card, db, user_id=uid)

        assert good > bad, (
            f"卡片级复习记录没有计入成功率：全对={good}，全错={bad}"
        )

    async def test_reviewed_state_without_next_review_still_scored(self, test_db):
        """★ 复习过但排期被清空的卡片，仍然要有掌握度

        `next_review_at` 是**调度**字段（NULL 的语义是"立即可复习"），
        它的有无并不表示"复习过没有"。旧实现要求它非空，于是这类卡被算成 0。

        真库实测（2026-09-11）：191 张有复习记录的卡片，`interval_days`
        全部是 1、最近复习在 80 天前 —— 旧指数曲线给 `2^(-80) ≈ 8e-25`，
        1183 张卡的掌握度因此**全部为 0.0**，字段依旧不携带信息。
        这是 3.9 必须换曲线的直接证据。
        """
        uid = await _make_user(test_db)
        card_id, _ = await _make_card(test_db, uid)

        async with test_db() as db:
            db.add(ReviewState(
                user_id=uid, item_type=ITEM_TYPE_CARD, item_id=card_id,
                interval_days=1, repetition=1, easiness_factor=2.5,
                stability=1.0, difficulty=5.0,
                last_reviewed_at=datetime.now(timezone.utc) - timedelta(days=80),
                next_review_at=None, review_count=1,
                state=ReviewStateKind.learning,
            ))
            await db.commit()

        async with test_db() as db:
            score = await mastery_service.compute_card_mastery(card_id, db, user_id=uid)
        # R(80, S=1) = 0.225；成功率取兜底（无日志 → 用 R）
        assert score > 0, "已复习但没有排期的卡片被判成 0 分"
        assert score < 100

    async def test_review_logs_are_scoped_by_user(self, test_db):
        """**跨用户隔离**：别人的复习记录不得影响我的掌握度

        旧实现的 `ReviewLog.quiz_id.in_(quiz_ids)` 没有 user_id 过滤。
        这里构造"另一个用户在同一个 quiz_id 上答错"，我的掌握度不应受影响。
        """
        uid = await _make_user(test_db)
        other = await _make_user(test_db)
        card_id, note_id = await _make_card(test_db, uid)
        quiz_id = await _make_quiz(
            test_db, uid, card_id, note_id,
            interval=10, repetition=2, last_reviewed_days_ago=0,
            next_review_at=datetime.now(timezone.utc) + timedelta(days=10),
        )

        async with test_db() as db:
            # 我：全对
            for _ in range(5):
                db.add(ReviewLog(
                    user_id=uid, quiz_id=quiz_id, note_id=note_id,
                    user_answer="a", is_correct=True, quality=5,
                    self_rating=5, grading_method="self_rating",
                    time_spent_ms=100, review_at=datetime.now(timezone.utc),
                ))
            await db.commit()
        async with test_db() as db:
            clean_score = await mastery_service.compute_card_mastery(card_id, db, user_id=uid)

        # 另一个用户在同一个 quiz_id 上留下大量错误记录
        async with test_db() as db:
            for _ in range(20):
                db.add(ReviewLog(
                    user_id=other, quiz_id=quiz_id, note_id=note_id,
                    user_answer="b", is_correct=False, quality=0,
                    self_rating=0, grading_method="self_rating",
                    time_spent_ms=100, review_at=datetime.now(timezone.utc),
                ))
            await db.commit()
        async with test_db() as db:
            polluted_score = await mastery_service.compute_card_mastery(card_id, db, user_id=uid)

        assert polluted_score == clean_score, (
            f"掌握度被其他用户的复习记录污染了：{clean_score} -> {polluted_score}"
        )

    async def test_naive_datetime_does_not_crash(self, test_db):
        """历史行的 naive datetime 不得导致减法崩掉

        SQLite 不存时区，老数据读出来是 naive。naive 与 aware 相减会抛
        `TypeError`，而这只在"库里只有老数据"时才出现，很容易漏测。
        """
        uid = await _make_user(test_db)
        card_id, note_id = await _make_card(test_db, uid)
        quiz_id = await _make_quiz(test_db, uid, card_id, note_id, interval=7, repetition=2)
        # 直接写 naive 值（绕过 ORM 的时区归一）
        async with test_db() as db:
            from sqlalchemy import text
            naive = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
            await db.execute(
                text("UPDATE quiz_items SET last_reviewed_at = :v, next_review_at = :n WHERE id = :i"),
                {"v": naive, "n": naive + timedelta(days=7), "i": quiz_id},
            )
            await db.commit()

        async with test_db() as db:
            score = await mastery_service.compute_card_mastery(card_id, db, user_id=uid)
        assert 0.0 <= score <= 100.0

    async def test_refresh_rejects_wrong_owner(self, test_db):
        """归属不符时拒绝写入（防止传错 ID 把别人的卡片改分）"""
        uid = await _make_user(test_db)
        other = await _make_user(test_db)
        card_id, _ = await _make_card(test_db, uid)

        async with test_db() as db:
            await mastery_service.refresh_card_mastery(card_id, db, user_id=other)
        assert await _card_mastery(test_db, card_id) == 0.0

    async def test_recalibrate_updates_stale_values(self, test_db):
        """重算入口必须真的把旧值改掉（公式变更后的迁移依赖它）"""
        uid = await _make_user(test_db)
        card_id, note_id = await _make_card(test_db, uid)
        await _make_quiz(
            test_db, uid, card_id, note_id,
            interval=10, repetition=3, last_reviewed_days_ago=0,
            next_review_at=datetime.now(timezone.utc) + timedelta(days=10),
        )
        # 模拟旧公式留下的错误值（1078/1183 为 0 的现场）
        async with test_db() as db:
            card = (await db.execute(
                select(KnowledgeCard).where(KnowledgeCard.id == card_id)
            )).scalars().first()
            card.mastery_level = 0.0
            await db.commit()

        async with test_db() as db:
            result = await mastery_service.recalibrate_all_mastery(db, user_id=uid)

        assert result["scanned"] >= 1
        assert await _card_mastery(test_db, card_id) > 0, "重算没有更新旧值"


# ---------------------------------------------------------------------------
# 3.1 ReviewState
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestReviewState:

    async def test_bootstrap_copies_legacy_scheduling_fields(self, test_db):
        """惰性补建必须**继承**旧字段，否则老数据复习进度归零"""
        uid = await _make_user(test_db)
        card_id, note_id = await _make_card(test_db, uid)
        due = datetime.now(timezone.utc) + timedelta(days=6)
        quiz_id = await _make_quiz(
            test_db, uid, card_id, note_id,
            interval=6, repetition=2, ef=2.7,
            last_reviewed_days_ago=1, next_review_at=due,
        )

        async with test_db() as db:
            state = await review_state_service.get_state(db, uid, ITEM_TYPE_QUIZ, quiz_id)

        assert state is not None
        assert state.interval_days == 6
        assert state.repetition == 2
        assert state.easiness_factor == pytest.approx(2.7)
        assert state.next_review_at is not None

    async def test_state_is_unique_per_item(self, test_db):
        """重复调用只应有一条状态（唯一约束生效）"""
        uid = await _make_user(test_db)
        card_id, _ = await _make_card(test_db, uid)

        async with test_db() as db:
            await review_state_service.get_state(db, uid, ITEM_TYPE_CARD, card_id)
        async with test_db() as db:
            await review_state_service.get_state(db, uid, ITEM_TYPE_CARD, card_id)

        async with test_db() as db:
            rows = (await db.execute(
                select(ReviewState).where(
                    ReviewState.user_id == uid,
                    ReviewState.item_type == ITEM_TYPE_CARD,
                    ReviewState.item_id == card_id,
                )
            )).scalars().all()
        assert len(rows) == 1, f"同一学习项存在多条状态：{len(rows)}"

    async def test_card_and_quiz_states_are_separate(self, test_db):
        """同一张卡片的 'card' 与 'quiz' 状态必须互不干扰

        两者 item_id 都是 UUID，只有 item_type 能区分。若唯一约束漏掉
        item_type，卡片状态会被题目状态覆盖。
        """
        uid = await _make_user(test_db)
        card_id, note_id = await _make_card(test_db, uid)
        quiz_id = await _make_quiz(test_db, uid, card_id, note_id)

        async with test_db() as db:
            card_state = await review_state_service.get_state(db, uid, ITEM_TYPE_CARD, card_id)
            quiz_state = await review_state_service.get_state(db, uid, ITEM_TYPE_QUIZ, quiz_id)
        assert card_state is not None and quiz_state is not None
        assert card_state.id != quiz_state.id

    async def test_apply_result_dual_writes(self, test_db):
        """`apply_sm2_result` 必须同时更新 review_states 与 quiz_items 旧字段

        双写是**可回退**的前提：任何时刻回滚代码，旧字段都还是正确值。
        """
        uid = await _make_user(test_db)
        card_id, note_id = await _make_card(test_db, uid)
        quiz_id = await _make_quiz(test_db, uid, card_id, note_id)
        due = datetime.now(timezone.utc) + timedelta(days=6)

        async with test_db() as db:
            state = await review_state_service.apply_sm2_result(
                db, uid, ITEM_TYPE_QUIZ, quiz_id,
                quality=5, interval_days=6, repetition=1,
                easiness_factor=2.6, next_review_at=due,
            )
            await db.commit()
            assert state is not None

        async with test_db() as db:
            quiz = (await db.execute(
                select(QuizItem).where(QuizItem.id == quiz_id)
            )).scalars().first()
        assert quiz.interval == 6
        assert quiz.repetition == 1
        assert quiz.easiness_factor == pytest.approx(2.6)

    async def test_failure_increments_lapses(self, test_db):
        """答错要累计 lapses（阶段 3.8 的 leech 检测依赖它）"""
        uid = await _make_user(test_db)
        card_id, _ = await _make_card(test_db, uid)
        due = datetime.now(timezone.utc) + timedelta(days=1)

        async with test_db() as db:
            await review_state_service.apply_sm2_result(
                db, uid, ITEM_TYPE_CARD, card_id,
                quality=0, interval_days=1, repetition=0,
                easiness_factor=2.3, next_review_at=due,
            )
            await db.commit()

        async with test_db() as db:
            state = await review_state_service.get_state(db, uid, ITEM_TYPE_CARD, card_id)
        assert state.lapses == 1
        assert state.state == ReviewStateKind.relearning

    async def test_due_list_excludes_future_items(self, test_db):
        """到期队列不得包含未来才该复习的项"""
        uid = await _make_user(test_db)
        card_id, _ = await _make_card(test_db, uid)

        async with test_db() as db:
            state = await review_state_service.get_state(db, uid, ITEM_TYPE_CARD, card_id)
            state.next_review_at = datetime.now(timezone.utc) + timedelta(days=30)
            await db.commit()

        async with test_db() as db:
            due = await review_state_service.list_due_states(db, uid, item_type=ITEM_TYPE_CARD)
        assert card_id not in [s.item_id for s in due]

    async def test_due_list_includes_null_next_review(self, test_db):
        """next_review_at 为 NULL 表示"立即可复习"（与旧代码判定一致）"""
        uid = await _make_user(test_db)
        card_id, _ = await _make_card(test_db, uid)

        async with test_db() as db:
            state = await review_state_service.get_state(db, uid, ITEM_TYPE_CARD, card_id)
            assert state.next_review_at is None

        async with test_db() as db:
            due = await review_state_service.list_due_states(db, uid, item_type=ITEM_TYPE_CARD)
        assert card_id in [s.item_id for s in due]


# ---------------------------------------------------------------------------
# 3.12 卡片直接复习（HTTP 契约）
# ---------------------------------------------------------------------------

class TestCardReviewAPI:
    """卡片复习接口

    每个用例用独立客户端 IP：限流规则按 IP 计数，共用 IP 会让后面的用例
    收到 429（表现为顺序相关的莫名失败）。
    """

    _ip_seq = 200

    def _client(self):
        from fastapi.testclient import TestClient

        from app.main import app

        type(self)._ip_seq += 1
        return TestClient(app, client=(f"198.51.100.{type(self)._ip_seq}", 9401))

    def _auth(self) -> tuple[dict, str]:
        import re

        from fastapi.testclient import TestClient  # noqa: F401

        suffix = uuid.uuid4().hex[:8]
        resp = self._client().post("/api/auth/register", json={
            "email": f"card{suffix}@example.com",
            "username": f"card{suffix}",
            "password": "CardPass123!",
        })
        assert resp.status_code in (200, 201), resp.text
        headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
        me = self._client().get("/api/auth/me", headers=headers).json()
        assert re.match(r"^[0-9a-f-]{36}$", me["id"])
        return headers, me["id"]

    def test_requires_auth(self, test_db):
        assert self._client().get("/api/review/cards/due").status_code == 401

    def test_due_cards_are_listed_after_migration(self, test_db):
        """**核心验收**：没有题目的卡片也要能进入复习队列"""
        import asyncio

        headers, uid = self._auth()
        card_id, _ = asyncio.run(_make_card(test_db, uid))
        # 建出卡片级状态（迁移脚本做的事）
        async def _boot():
            async with test_db() as db:
                await review_state_service.get_state(db, uid, ITEM_TYPE_CARD, card_id)
        asyncio.run(_boot())

        resp = self._client().get("/api/review/cards/due", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        ids = [i["card_id"] for i in body["items"]]
        assert card_id in ids, (
            "没有题目的卡片未出现在到期队列 —— 阶段 3.12 未生效"
        )

    def test_unknown_card_returns_404(self, test_db):
        headers, _ = self._auth()
        resp = self._client().post(
            f"/api/review/cards/{uuid.uuid4()}/submit",
            headers=headers, json={"self_rating": 4},
        )
        assert resp.status_code == 404

    @pytest.mark.parametrize("bad", [6, -1])
    def test_out_of_range_rating_rejected(self, test_db, bad):
        headers, _ = self._auth()
        resp = self._client().post(
            f"/api/review/cards/{uuid.uuid4()}/submit",
            headers=headers, json={"self_rating": bad},
        )
        assert resp.status_code == 422

    def test_submit_advances_state_and_mastery(self, test_db):
        """提交卡片复习必须推进状态并刷新掌握度"""
        import asyncio

        headers, uid = self._auth()
        card_id, _ = asyncio.run(_make_card(test_db, uid))

        async def _boot():
            async with test_db() as db:
                await review_state_service.get_state(db, uid, ITEM_TYPE_CARD, card_id)
        asyncio.run(_boot())

        resp = self._client().post(
            f"/api/review/cards/{card_id}/submit",
            headers=headers,
            json={"self_rating": 5, "user_answer": "想起来了", "time_spent_ms": 800},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["quality"] == 5
        assert body["is_correct"] is True
        assert body["interval_days"] >= 1
        assert body["next_review_at"] is not None
        assert body["mastery_level"] > 0, "卡片复习后掌握度仍为 0"
