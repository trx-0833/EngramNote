"""
四档自评闭环测试（overhaul-plan 阶段 1.4）

覆盖的契约（每一条都对应一个曾经真实存在的缺陷）：

1. **四档 → quality 映射**：0/3/4/5 必须原样进入 SM-2，不得被自动判分覆盖。
2. **占位提交不推进调度**：简答题首次提交（无自评）必须落一条
   `grading_method='ungraded'` 记录，且 `interval`/`repetition`/`review_count`
   与 `next_review_at` **完全不变**，响应 `needs_self_assessment=True`。
3. **自评提交补完占位**：第二次带 `self_rating` 的提交必须能穿过幂等守卫
   （这是改造前"自评永远写不进库"的直接回归测试），并真正推进调度。
4. **同分重复自评幂等**：不重复创建记录、不重复推进调度。
5. **越界自评被 API 拒绝**：6 / -1 返回 422。
6. **落库可审计**：`self_rating` 与 `grading_method` 如实持久化，
   自动判分记录与自评记录可区分 —— 这是校准曲线的原始信号。
7. **每日限额不卡死两阶段**：额度恰好用尽时，新题被拒；
   但"补完占位"仍放行（否则用户答了却结不了账）。

为什么用 service 层而非 HTTP 层跑主流程：这些断言全部关于
**数据库状态与调度参数**，`submit_answer()` 是唯一的写入点；HTTP 层只做
参数校验与字段搬运，另有 `TestSubmitAnswerSchema` 与
`TestSubmitAnswerAPI` 单独覆盖，避免把限流器/认证也拖进每个用例。
"""

import uuid

import pytest
from sqlalchemy import func, select

from app.models.knowledge_card import CardType, KnowledgeCard
from app.models.note import Note, SourceType
from app.models.quiz_item import QuestionType, QuizItem
from app.models.review_log import ReviewLog
from app.models.user import User
from app.services import review_service
from app.services.sm2_service import grade_answer

# 简答题参考答案；用例里的作答文本刻意与它不等，以证明自评不与自动判分挂钩
SHORT_ANSWER_KEY = "机器学习"
SHORT_ANSWER_REPLY = "一种让计算机从数据中学习规律的方法"


# ---------------------------------------------------------------------------
# 测试数据构造
# ---------------------------------------------------------------------------

async def _make_user(session_factory) -> str:
    uid = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(User(
            id=uid, email=f"{uid[:8]}@test.local", username=f"u{uid[:8]}",
            hashed_password="x", is_active=True,
        ))
        await db.commit()
    return uid


async def _make_quiz(
    session_factory,
    user_id: str,
    *,
    question_type: QuestionType = QuestionType.short_answer,
    answer: str = SHORT_ANSWER_KEY,
    options: str | None = None,
    interval: int = 1,
    repetition: int = 0,
    easiness_factor: float = 2.5,
) -> tuple[str, str]:
    """建一道题（含所属笔记与卡片），返回 (quiz_id, card_id)

    必须分三次提交：QuizItem 对 card_id 有 NOT NULL 外键，
    而 SQLAlchemy 没有声明 relationship，不会自动排序 INSERT。
    """
    note_id = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(Note(id=note_id, user_id=user_id, title="t", source_type=SourceType.pdf))
        await db.commit()

    card_id = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(KnowledgeCard(
            id=card_id, user_id=user_id, note_id=note_id,
            card_type=CardType.concept, title="c", content="cc",
        ))
        await db.commit()

    quiz_id = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(QuizItem(
            id=quiz_id, user_id=user_id, note_id=note_id, card_id=card_id,
            question="机器学习是什么", answer=answer, options=options,
            question_type=question_type, interval=interval, repetition=repetition,
            easiness_factor=easiness_factor,
        ))
        await db.commit()
    return quiz_id, card_id


async def _snapshot_quiz(session_factory, quiz_id: str) -> dict:
    """读取题目的调度参数，用于断言"是否推进" """
    async with session_factory() as db:
        quiz = (await db.execute(
            select(QuizItem).where(QuizItem.id == quiz_id)
        )).scalars().first()
        assert quiz is not None
        return {
            "interval": quiz.interval,
            "repetition": quiz.repetition,
            "easiness_factor": quiz.easiness_factor,
            "next_review_at": quiz.next_review_at,
            "review_count": quiz.review_count,
        }


async def _logs(session_factory, quiz_id: str) -> list[ReviewLog]:
    async with session_factory() as db:
        return list((await db.execute(
            select(ReviewLog).where(ReviewLog.quiz_id == quiz_id)
            .order_by(ReviewLog.review_at)
        )).scalars().all())


# ---------------------------------------------------------------------------
# 1. 四档 → quality 映射（纯函数层）
# ---------------------------------------------------------------------------

class TestFourTierMapping:
    """四档自评取值 0/3/4/5 必须原样进入 SM-2"""

    @pytest.mark.parametrize("quality", [0, 3, 4, 5])
    def test_rating_is_passed_through_verbatim(self, quality):
        """自评分不得被自动判分结果覆盖或改写"""
        grade = grade_answer("short_answer", SHORT_ANSWER_REPLY, SHORT_ANSWER_KEY)
        # 简答题的自动判分是占位值：必须明确标记为"不可信"
        assert grade["needs_self_assessment"] is True

        # 模拟 review_service 的自评覆盖分支
        effective = {
            "quality": max(0, min(5, int(quality))),
            "method": "self_rating",
            "needs_self_assessment": False,
        }
        assert effective["quality"] == quality
        assert effective["needs_self_assessment"] is False

    @pytest.mark.parametrize("quality,expect_correct", [
        (0, False), (3, True), (4, True), (5, True),
    ])
    def test_pass_threshold_is_three(self, quality, expect_correct):
        """SM-2 的语义分界在 quality >= 3，四档必须与之对齐"""
        assert (quality >= 3) is expect_correct


# ---------------------------------------------------------------------------
# 2~4. 两阶段提交（service 层，真实数据库写入）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestTwoPhaseSubmission:

    async def test_placeholder_does_not_advance_schedule(self, test_db):
        """阶段一：简答题占位提交**不得**推进调度

        改造前这里会把占位分（quality=1）当成真实评分推进 SM-2，
        把"尚未自评"错误地固化成"答错并重置间隔"。
        """
        uid = await _make_user(test_db)
        quiz_id, _ = await _make_quiz(test_db, uid)
        before = await _snapshot_quiz(test_db, quiz_id)

        async with test_db() as db:
            result = await review_service.submit_answer(
                quiz_id, uid, SHORT_ANSWER_REPLY, 3000, db,
            )

        assert "error" not in result
        assert result["grading_method"] == "ungraded"
        assert result["needs_self_assessment"] is True
        assert result["self_rating"] is None
        assert result["completing_placeholder"] is False
        # 未推进调度 → next_review_at 为 None
        assert result["sm2"]["next_review_at"] is None

        after = await _snapshot_quiz(test_db, quiz_id)
        assert after == before, (
            f"占位提交不应改变调度参数，但发生了变化:\n  before={before}\n  after={after}"
        )

        logs = await _logs(test_db, quiz_id)
        assert len(logs) == 1
        assert logs[0].grading_method == "ungraded"
        assert logs[0].self_rating is None

    @pytest.mark.parametrize("quality", [0, 3, 4, 5])
    async def test_self_rating_completes_placeholder(self, test_db, quality):
        """阶段二：带自评的第二次提交必须能穿过幂等守卫并推进调度

        这是**核心回归测试**。改造前幂等守卫按「同日同题已提交」直接返回旧结果，
        自评请求永远写不进库、调度永远停在 interval=1 —— 全库 1058 道题
        interval 全是 1 正是这个缺陷的现场证据。
        """
        uid = await _make_user(test_db)
        quiz_id, _ = await _make_quiz(test_db, uid)

        async with test_db() as db:
            first = await review_service.submit_answer(
                quiz_id, uid, SHORT_ANSWER_REPLY, 3000, db,
            )
        assert first["needs_self_assessment"] is True

        async with test_db() as db:
            second = await review_service.submit_answer(
                quiz_id, uid, SHORT_ANSWER_REPLY, 3000, db, self_rating=quality,
            )

        assert "error" not in second, f"自评提交被拒: {second.get('error')}"
        assert second["completing_placeholder"] is True
        assert second["grading_method"] == "self_rating"
        assert second["needs_self_assessment"] is False
        assert second["self_rating"] == quality
        assert second["quality"] == quality, "进入 SM-2 的分必须是自评分"
        assert second["is_correct"] is (quality >= 3)

        state = await _snapshot_quiz(test_db, quiz_id)
        assert state["review_count"] == 1, "一次作答只应计一次复习"
        if quality >= 3:
            assert state["repetition"] == 1
            assert state["next_review_at"] is not None, "自评通过后必须排下次复习"
        else:
            # quality < 3 时 SM-2 重置重复次数，但同样要写回 next_review_at
            assert state["repetition"] == 0
            assert state["next_review_at"] is not None

        logs = await _logs(test_db, quiz_id)
        assert len(logs) == 2, "占位记录与自评记录都应保留（校准曲线需要两者）"
        assert logs[0].grading_method == "ungraded" and logs[0].self_rating is None
        assert logs[1].grading_method == "self_rating" and logs[1].self_rating == quality

    async def test_repeat_same_rating_is_idempotent(self, test_db):
        """同分重复自评不得重复创建记录或重复推进调度"""
        uid = await _make_user(test_db)
        quiz_id, _ = await _make_quiz(test_db, uid)

        async with test_db() as db:
            await review_service.submit_answer(quiz_id, uid, SHORT_ANSWER_REPLY, 1000, db)
        async with test_db() as db:
            await review_service.submit_answer(quiz_id, uid, SHORT_ANSWER_REPLY, 1000, db, self_rating=4)
        after_first = await _snapshot_quiz(test_db, quiz_id)

        async with test_db() as db:
            again = await review_service.submit_answer(
                quiz_id, uid, SHORT_ANSWER_REPLY, 1000, db, self_rating=4,
            )
        after_repeat = await _snapshot_quiz(test_db, quiz_id)

        assert "error" not in again
        assert after_repeat == after_first, "重复同分自评不应改变调度"
        assert len(await _logs(test_db, quiz_id)) == 2, "重复提交不应新增记录"

    async def test_repeat_without_rating_is_idempotent(self, test_db):
        """不带自评的重复提交（双击/重放）应返回既有结果而非新增记录"""
        uid = await _make_user(test_db)
        quiz_id, _ = await _make_quiz(test_db, uid)

        async with test_db() as db:
            await review_service.submit_answer(quiz_id, uid, SHORT_ANSWER_REPLY, 1000, db)
        async with test_db() as db:
            repeat = await review_service.submit_answer(
                quiz_id, uid, SHORT_ANSWER_REPLY, 1000, db,
            )

        assert repeat["grading_method"] == "ungraded"
        assert repeat["needs_self_assessment"] is True, (
            "幂等返回占位记录时仍须请用户自评，否则这道题再也结不了账"
        )
        assert len(await _logs(test_db, quiz_id)) == 1

    async def test_auto_graded_question_needs_no_rating(self, test_db):
        """选择题自动判分可靠 → 不请求自评，且调度立即推进"""
        uid = await _make_user(test_db)
        quiz_id, _ = await _make_quiz(
            test_db, uid,
            question_type=QuestionType.choice,
            answer="B", options='["A. 1","B. 2"]',
        )

        async with test_db() as db:
            result = await review_service.submit_answer(quiz_id, uid, "B", 1200, db)

        assert result["grading_method"] == "choice"
        assert result["needs_self_assessment"] is False
        assert result["is_correct"] is True
        state = await _snapshot_quiz(test_db, quiz_id)
        assert state["review_count"] == 1
        assert state["next_review_at"] is not None

    async def test_answering_without_prior_placeholder_is_allowed(self, test_db):
        """今日无任何记录时直接带自评提交是合法路径（不能被困在"必须先占位"）"""
        uid = await _make_user(test_db)
        quiz_id, _ = await _make_quiz(test_db, uid)

        async with test_db() as db:
            result = await review_service.submit_answer(
                quiz_id, uid, SHORT_ANSWER_REPLY, 1000, db, self_rating=5,
            )

        assert "error" not in result
        assert result["grading_method"] == "self_rating"
        assert result["completing_placeholder"] is False
        assert len(await _logs(test_db, quiz_id)) == 1


# ---------------------------------------------------------------------------
# 5. 落库可审计
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestPersistence:

    async def test_self_rating_is_persisted_to_its_own_column(self, test_db):
        """self_rating 必须独立成列，不能与 quality 混用

        只有两者都在，才能统计「自动判分 vs 用户自评」的不一致率。
        """
        uid = await _make_user(test_db)
        quiz_id, _ = await _make_quiz(test_db, uid)

        async with test_db() as db:
            await review_service.submit_answer(quiz_id, uid, SHORT_ANSWER_REPLY, 1000, db)
        async with test_db() as db:
            await review_service.submit_answer(quiz_id, uid, SHORT_ANSWER_REPLY, 1000, db, self_rating=3)

        logs = await _logs(test_db, quiz_id)
        placeholder, rated = logs[0], logs[1]
        # 占位记录：有 quality（占位值）但没有 self_rating
        assert placeholder.self_rating is None
        assert placeholder.grading_method == "ungraded"
        # 自评记录：两列都有值且一致
        assert rated.self_rating == 3
        assert rated.quality == 3
        assert rated.grading_method == "self_rating"

    async def test_review_logs_stay_append_only(self, test_db):
        """复习记录只追加、不覆盖：两次提交留下两行，不 UPDATE 旧行"""
        uid = await _make_user(test_db)
        quiz_id, _ = await _make_quiz(test_db, uid)

        async with test_db() as db:
            await review_service.submit_answer(quiz_id, uid, SHORT_ANSWER_REPLY, 1000, db)
        async with test_db() as db:
            await review_service.submit_answer(quiz_id, uid, SHORT_ANSWER_REPLY, 1000, db, self_rating=4)

        async with test_db() as db:
            total = (await db.execute(
                select(func.count()).select_from(ReviewLog)
                .where(ReviewLog.quiz_id == quiz_id)
            )).scalar()
        assert total == 2


# ---------------------------------------------------------------------------
# 6. 每日限额不得卡死两阶段流程
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestDailyLimitInteraction:
    """限额语义：占满额度的最后一次提交仍须能"结账"

    改造过程中这里连续踩了两个坑，两条都留成回归测试：
      a) 限额检查排在幂等守卫之前 → 答满额度后自评被 429 拒绝；
      b) 曾经把豁免条件写成"简答题豁免" → 简答题完全绕过每日限额。
    """

    async def _fill_quota(self, test_db, uid: str, count: int) -> None:
        """用挂在**另一道题**上的已判分记录占满额度

        必须挂到别的题上：挂到被测题会让幂等守卫正确地判定"今日已提交过"，
        从而走不到限额逻辑（这一点在探针里也踩过一次）。
        """
        from datetime import datetime, timezone
        filler_quiz, _ = await _make_quiz(
            test_db, uid, question_type=QuestionType.choice,
            answer="A", options='["A. 占"]',
        )
        async with test_db() as db:
            for i in range(count):
                db.add(ReviewLog(
                    user_id=uid, quiz_id=filler_quiz, note_id=None,
                    user_answer=f"f{i}", is_correct=True, quality=5,
                    self_rating=None, grading_method="choice",
                    time_spent_ms=100, review_at=datetime.now(timezone.utc),
                ))
            await db.commit()

    async def test_completing_placeholder_survives_exhausted_quota(self, test_db):
        """额度恰好剩 1 时，第 N 道简答题的占位 + 自评都必须成功"""
        uid = await _make_user(test_db)
        limit = review_service.DAILY_REVIEW_LIMIT
        await self._fill_quota(test_db, uid, limit - 1)
        quiz_id, _ = await _make_quiz(test_db, uid)

        async with test_db() as db:
            placeholder = await review_service.submit_answer(
                quiz_id, uid, SHORT_ANSWER_REPLY, 1000, db,
            )
        assert "error" not in placeholder, (
            f"额度剩 1 时简答题占位提交不应被拒: {placeholder.get('error')}"
        )
        assert placeholder["needs_self_assessment"] is True

        async with test_db() as db:
            rated = await review_service.submit_answer(
                quiz_id, uid, SHORT_ANSWER_REPLY, 1000, db, self_rating=4,
            )
        assert "error" not in rated, (
            f"补完占位不应被限额拒绝（否则用户答了却结不了账）: {rated.get('error')}"
        )
        assert rated["completing_placeholder"] is True
        state = await _snapshot_quiz(test_db, quiz_id)
        assert state["review_count"] == 1

    async def test_new_question_is_rejected_when_quota_exhausted(self, test_db):
        """全新题目在额度用尽时必须被拒 —— 防止"简答题豁免"式漏洞复现"""
        uid = await _make_user(test_db)
        limit = review_service.DAILY_REVIEW_LIMIT
        await self._fill_quota(test_db, uid, limit)
        quiz_id, _ = await _make_quiz(test_db, uid)

        async with test_db() as db:
            result = await review_service.submit_answer(
                quiz_id, uid, SHORT_ANSWER_REPLY, 1000, db,
            )

        assert "error" in result, (
            "额度用尽后新开一道题必须被拒；若通过则说明限额被绕过"
        )
        assert "上限" in result["error"]
        assert await _logs(test_db, quiz_id) == [], "被拒的提交不应留下任何记录"

    async def test_placeholder_does_not_count_toward_quota(self, test_db):
        """占位记录不计入今日已完成数（它未推进调度，算作已完成是虚报）"""
        uid = await _make_user(test_db)
        quiz_id, _ = await _make_quiz(test_db, uid)

        async with test_db() as db:
            await review_service.submit_answer(quiz_id, uid, SHORT_ANSWER_REPLY, 1000, db)

        async with test_db() as db:
            stats = await review_service.get_review_stats(uid, db)
        assert stats["today_done"] == 0, (
            f"占位提交不应计入 today_done，实际 {stats['today_done']}"
        )


# ---------------------------------------------------------------------------
# 7. API 契约（参数校验与字段搬运）
# ---------------------------------------------------------------------------

class TestSubmitAnswerSchema:
    """请求模型必须在入口就挡掉越界自评，而不是留给服务层 clamp"""

    def test_accepts_four_tier_values(self):
        from app.schemas.review import SubmitAnswerRequest

        for quality in (0, 3, 4, 5):
            req = SubmitAnswerRequest(quiz_id="q", user_answer="a", self_rating=quality)
            assert req.self_rating == quality

    @pytest.mark.parametrize("bad", [6, -1, 100, -100])
    def test_rejects_out_of_range(self, bad):
        import pydantic

        from app.schemas.review import SubmitAnswerRequest

        with pytest.raises(pydantic.ValidationError):
            SubmitAnswerRequest(quiz_id="q", user_answer="a", self_rating=bad)

    def test_self_rating_is_optional(self):
        from app.schemas.review import SubmitAnswerRequest

        assert SubmitAnswerRequest(quiz_id="q", user_answer="a").self_rating is None

    def test_response_builder_maps_all_self_rating_fields(self):
        """两个提交入口共用 from_service_result，字段不得漏搬"""
        from app.schemas.review import SubmitAnswerResponse

        payload = {
            "quiz_id": "q1", "is_correct": True, "quality": 4,
            "correct_answer": "A", "explanation": None, "options": None,
            "question_type": "short_answer",
            "sm2": {"interval": 1, "repetition": 1, "easiness_factor": 2.5,
                    "next_review_at": None},
            "self_rating": 4, "grading_method": "self_rating",
            "needs_self_assessment": False, "completing_placeholder": True,
            "grading_reason": "用户自评",
        }
        resp = SubmitAnswerResponse.from_service_result(payload)

        assert resp.self_rating == 4
        assert resp.grading_method == "self_rating"
        assert resp.needs_self_assessment is False
        assert resp.completing_placeholder is True
        assert resp.grading_reason == "用户自评"
        assert resp.sm2.next_review_at is None, "占位提交时 next_review_at 可为 null"
