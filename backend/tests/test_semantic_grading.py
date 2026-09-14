"""
简答题语义判分测试（overhaul-plan 阶段 3.5）

## 这个模块守的是什么

简答题判分是 L-1（"逻辑完全相反的答案被判为回忆成功"）的修复点，
也是 L-3（"无法验证产品是否有效"）的前置 —— 没有可信的判分，
保持率与校准曲线都是噪声。

而它的失效模式**特别隐蔽**：

- LLM 判分失败时若被当成"答错"，会让整个简答题池的间隔被误重置
  （用户明明答对了，复习进度却被清空）
- 置信度不足时若勉强采信，会让错误判定**进入调度**
  （间隔一旦改变就无法事后纠正）
- `partial` 若映射到 quality<3，会让"说对一半"被算作答错并重置间隔

因此这里重点锁**降级行为与映射口径**，而不是"能不能调通 LLM"。
"""

import json

import pytest

from app.services.sm2_service import (
    HIGH_CONFIDENCE,
    SEMANTIC_CONFIDENCE_THRESHOLD,
    VERDICT_TO_QUALITY,
    grade_answer,
    grade_short_answer_semantically,
)


class TestVerdictMapping:
    """verdict → quality 的映射口径（决定调度后果）"""

    def test_partial_is_passing_not_failing(self):
        """**关键**：partial 必须 >= 3（算答对），不能重置间隔

        SM-2 里 `quality >= 3` 才算答对。把"说对一半"判成 2 会让它算作答错、
        **重置间隔** —— 用户明明记住了一半却被当作完全没记住重新开始。
        这比"把半分当及格"更伤：过早重置会让长期复习永远推进不下去。
        """
        assert VERDICT_TO_QUALITY["partial"] >= 3, (
            "partial 被映射为不及格 —— 会让'说对一半'重置复习间隔"
        )

    def test_incorrect_is_failing(self):
        assert VERDICT_TO_QUALITY["incorrect"] < 3

    def test_correct_is_passing(self):
        assert VERDICT_TO_QUALITY["correct"] >= 4

    def test_threshold_is_reasonable(self):
        """置信度阈值必须在 (0,1) 且高置信度阈值更高"""
        assert 0.0 < SEMANTIC_CONFIDENCE_THRESHOLD < 1.0
        assert HIGH_CONFIDENCE > SEMANTIC_CONFIDENCE_THRESHOLD


@pytest.mark.asyncio
class TestSemanticGrading:
    """`grade_short_answer_semantically` 的降级行为"""

    async def test_empty_answer_short_circuits(self):
        """空答案不调用 LLM 即判错（省一次额度，且语义上确定）"""
        result = await grade_short_answer_semantically(
            question="什么是浮充？", expected_answer="蓄电池的一种运行方式",
            user_answer="   ",
        )
        assert result is not None
        assert result["quality"] == 0
        assert result["needs_self_assessment"] is False

    async def test_llm_failure_returns_none_not_wrong(self, monkeypatch):
        """**关键**：LLM 失败必须返回 None（未判分），不能返回"答错"

        把它当答错会让用户答对的题被重置间隔 —— 比"没判分"严重得多。
        """
        from app.services import llm_service as llm_mod

        class Boom:
            def __init__(self, *a, **k):
                pass

            async def grade_short_answer(self, **kw):
                raise RuntimeError("网关不可用")

        monkeypatch.setattr(llm_mod, "LLMService", Boom)

        result = await grade_short_answer_semantically(
            question="q", expected_answer="a", user_answer="u",
        )
        assert result is None, "LLM 失败时返回了判分结果 —— 会被误当作答错"

    async def test_low_confidence_returns_none(self, monkeypatch):
        """置信度不足必须退回自评，而不是勉强采信

        勉强采信会让不确定的判定进入调度，而间隔一旦改变**无法事后纠正**。
        """
        from app.services import llm_service as llm_mod

        class LowConf:
            def __init__(self, *a, **k):
                pass

            async def grade_short_answer(self, **kw):
                return {
                    "verdict": "correct",
                    "missing_points": [], "misconceptions": [],
                    "confidence": SEMANTIC_CONFIDENCE_THRESHOLD - 0.01,
                    "reason": "不太确定",
                }

        monkeypatch.setattr(llm_mod, "LLMService", LowConf)

        result = await grade_short_answer_semantically(
            question="q", expected_answer="a", user_answer="u",
        )
        assert result is None

    async def test_at_threshold_is_accepted(self, monkeypatch):
        """恰好等于阈值应当被接受（边界包含语义）"""
        from app.services import llm_service as llm_mod

        class AtThreshold:
            def __init__(self, *a, **k):
                pass

            async def grade_short_answer(self, **kw):
                return {
                    "verdict": "partial",
                    "missing_points": ["第二点"],
                    "misconceptions": [],
                    "confidence": SEMANTIC_CONFIDENCE_THRESHOLD,
                    "reason": "部分正确",
                }

        monkeypatch.setattr(llm_mod, "LLMService", AtThreshold)

        result = await grade_short_answer_semantically(
            question="q", expected_answer="a", user_answer="u",
        )
        assert result is not None
        assert result["quality"] == VERDICT_TO_QUALITY["partial"]

    async def test_high_confidence_correct_gets_five(self, monkeypatch):
        """高置信度 + correct → quality 5（间隔增长最快）"""
        from app.services import llm_service as llm_mod

        class HighConf:
            def __init__(self, *a, **k):
                pass

            async def grade_short_answer(self, **kw):
                return {
                    "verdict": "correct", "missing_points": [],
                    "misconceptions": [], "confidence": HIGH_CONFIDENCE,
                    "reason": "完全一致",
                }

        monkeypatch.setattr(llm_mod, "LLMService", HighConf)
        result = await grade_short_answer_semantically(
            question="q", expected_answer="a", user_answer="u",
        )
        assert result["quality"] == 5

    async def test_medium_confidence_correct_gets_four(self, monkeypatch):
        """置信度一般但判定 correct → quality 4（增长稍慢，作为纠错余量）"""
        from app.services import llm_service as llm_mod

        class MidConf:
            def __init__(self, *a, **k):
                pass

            async def grade_short_answer(self, **kw):
                return {
                    "verdict": "correct", "missing_points": [],
                    "misconceptions": [],
                    "confidence": (SEMANTIC_CONFIDENCE_THRESHOLD + HIGH_CONFIDENCE) / 2,
                    "reason": "含义一致",
                }

        monkeypatch.setattr(llm_mod, "LLMService", MidConf)
        result = await grade_short_answer_semantically(
            question="q", expected_answer="a", user_answer="u",
        )
        assert result["quality"] == 4

    async def test_missing_points_surface_in_reason(self, monkeypatch):
        """缺失点必须出现在给用户看的 reason 里

        只说"部分正确"没有指导价值 —— 用户需要知道**缺了哪一点**。
        这正是"不要 0-100 分、要 missing_points"的落地。
        """
        from app.services import llm_service as llm_mod

        class Partial:
            def __init__(self, *a, **k):
                pass

            async def grade_short_answer(self, **kw):
                return {
                    "verdict": "partial",
                    "missing_points": ["接地刀闸", "隔离刀闸"],
                    "misconceptions": [],
                    "confidence": 0.9,
                    "reason": "漏了两种刀闸",
                }

        monkeypatch.setattr(llm_mod, "LLMService", Partial)
        result = await grade_short_answer_semantically(
            question="操作术语有哪些？", expected_answer="断路器/隔离刀闸/接地刀闸",
            user_answer="断路器：合上、断开",
        )
        assert "接地刀闸" in result["reason"]
        assert "隔离刀闸" in result["reason"]

    async def test_misconceptions_surface_in_reason(self, monkeypatch):
        """误解点同样要露出（答错时告诉用户错在哪）"""
        from app.services import llm_service as llm_mod

        class Wrong:
            def __init__(self, *a, **k):
                pass

            async def grade_short_answer(self, **kw):
                return {
                    "verdict": "incorrect", "missing_points": [],
                    "misconceptions": ["把浮充说成了均充"],
                    "confidence": 0.95, "reason": "概念混淆",
                }

        monkeypatch.setattr(llm_mod, "LLMService", Wrong)
        result = await grade_short_answer_semantically(
            question="q", expected_answer="浮充", user_answer="均充",
        )
        assert "均充" in result["reason"]

    async def test_result_shape_matches_grade_answer(self, monkeypatch):
        """返回结构必须与 `grade_answer` 同构（调用方共用一套口径）"""
        from app.services import llm_service as llm_mod

        class Ok:
            def __init__(self, *a, **k):
                pass

            async def grade_short_answer(self, **kw):
                return {
                    "verdict": "correct", "missing_points": [],
                    "misconceptions": [], "confidence": 0.9, "reason": "一致",
                }

        monkeypatch.setattr(llm_mod, "LLMService", Ok)
        result = await grade_short_answer_semantically(
            question="q", expected_answer="a", user_answer="u",
        )
        # 与 grade_answer 的返回键必须一致，否则调用方要用两套逻辑读它
        reference = grade_answer("short_answer", "u", "a")
        assert set(result.keys()) >= set(reference.keys()) - {"detail"}
        assert result["method"] == "semantic"
        assert result["needs_self_assessment"] is False


class TestShortAnswerStillUngradedByDefault:
    """`grade_answer` 对简答题仍返回占位（语义判分是**可选增强**，不是替换）"""

    def test_pure_function_still_returns_ungraded(self):
        """纯函数不得偷偷变成需要 IO 的东西

        `grade_answer` 被多处同步调用，把它改成 async 会波及整个调用链。
        语义判分作为独立的 async 函数存在，由调用方显式选择。
        """
        result = grade_answer("short_answer", "一些回答", "标准答案")
        assert result["needs_self_assessment"] is True
        assert result["method"] == "ungraded"


@pytest.mark.asyncio
class TestGradingDetailReachesTheClient:
    """★ 回归：判分明细必须**真的出现在响应里**

    改造前 service 会在返回前补一句 `result["grading_detail"] = grade["detail"]`，
    但 `SubmitAnswerResponse` **没有声明这个字段**，Pydantic 静默丢弃 ——
    LLM 判分明细算好了、落库了，前端却永远拿不到。

    这类缺陷的症状极具误导性："功能看起来做好了，界面毫无反应"。
    所以这里断言的是**响应对象**上的字段，而不是 service 返回的 dict
    （dict 一直是有的，正因如此单测此前全绿）。
    """

    async def test_detail_is_present_and_typed(self, monkeypatch, test_db):
        from app.models.knowledge_card import CardType, KnowledgeCard
        from app.models.note import Note, SourceType
        from app.models.quiz_item import QuestionType, QuizItem
        from app.models.user import User
        from app.schemas.review import SubmitAnswerResponse
        from app.services import llm_service as llm_mod
        from app.services import review_service

        class Partial:
            def __init__(self, *a, **k):
                pass

            async def grade_short_answer(self, **kw):
                return {
                    "verdict": "partial",
                    "missing_points": ["接地刀闸"],
                    "misconceptions": ["把浮充说成均充"],
                    "confidence": 0.92,
                    "reason": "漏了一种刀闸",
                }

        monkeypatch.setattr(llm_mod, "LLMService", Partial)

        import uuid
        uid = str(uuid.uuid4())
        note_id = str(uuid.uuid4())
        card_id = str(uuid.uuid4())
        quiz_id = str(uuid.uuid4())
        async with test_db() as db:
            db.add(User(id=uid, email=f"{uid[:8]}@e.com", username=f"u{uid[:8]}",
                        hashed_password="x", is_active=True))
            await db.commit()
        async with test_db() as db:
            db.add(Note(id=note_id, user_id=uid, title="t", source_type=SourceType.pdf))
            await db.commit()
        async with test_db() as db:
            db.add(KnowledgeCard(id=card_id, user_id=uid, note_id=note_id,
                                 card_type=CardType.concept, title="浮充", content="c"))
            await db.commit()
        async with test_db() as db:
            db.add(QuizItem(
                id=quiz_id, user_id=uid, note_id=note_id, card_id=card_id,
                question="操作术语有哪些", answer="断路器/隔离刀闸/接地刀闸",
                question_type=QuestionType.short_answer,
            ))
            await db.commit()

        async with test_db() as db:
            result = await review_service.submit_answer(
                quiz_id, uid, "断路器", 0, db, use_semantic_grading=True,
            )

        assert result.get("grading_detail"), "service 层没有产出判分明细"
        resp = SubmitAnswerResponse.from_service_result(result)
        assert resp.grading_detail is not None, (
            "判分明细没有出现在响应对象上 —— 响应模型漏声明字段，Pydantic 会静默丢弃"
        )
        # 阶段 5.1：`grading_detail` 从 `Dict[str, Any]` 收紧成 `GradingDetail` 模型
        # （补掉漂移报告里的 SCHEMA_LOOSE 空壳），因此这里按**属性**读而不是下标读。
        # 断言本身没变：仍然在验"字段真的到了响应对象上、且值没被改过"。
        # 序列化出去的 JSON 与收紧前逐字节相同（见 schemas/review.py 的
        # `GradingDetail._omit_unset_none`），所以这是类型收紧而非接口变更。
        assert resp.grading_detail.verdict == "partial"
        assert resp.grading_detail.missing_points == ["接地刀闸"]
        assert resp.grading_detail.misconceptions == ["把浮充说成均充"]
        # 再补一条"越过了响应模型也还是同一份 JSON"的证据，防止将来有人
        # 把 GradingDetail 的序列化行为改坏（例如顺手加 exclude_none）。
        assert json.loads(resp.model_dump_json())["grading_detail"] == {
            "verdict": "partial",
            "missing_points": ["接地刀闸"],
            "misconceptions": ["把浮充说成均充"],
            "confidence": 0.92,
            "reason": "漏了一种刀闸",
        }

    async def test_placeholder_response_has_null_detail(self, test_db):
        """未判分时必须是 `null`，不能是空对象

        空对象会渲染成"判分过、但没发现任何问题"，与"根本没判分"是两回事。
        """
        from app.models.knowledge_card import CardType, KnowledgeCard
        from app.models.note import Note, SourceType
        from app.models.quiz_item import QuestionType, QuizItem
        from app.models.user import User
        from app.schemas.review import SubmitAnswerResponse
        from app.services import review_service

        import uuid
        uid = str(uuid.uuid4())
        note_id = str(uuid.uuid4())
        card_id = str(uuid.uuid4())
        quiz_id = str(uuid.uuid4())
        async with test_db() as db:
            db.add(User(id=uid, email=f"{uid[:8]}@e.com", username=f"u{uid[:8]}",
                        hashed_password="x", is_active=True))
            await db.commit()
        async with test_db() as db:
            db.add(Note(id=note_id, user_id=uid, title="t", source_type=SourceType.pdf))
            await db.commit()
        async with test_db() as db:
            db.add(KnowledgeCard(id=card_id, user_id=uid, note_id=note_id,
                                 card_type=CardType.concept, title="t", content="c"))
            await db.commit()
        async with test_db() as db:
            db.add(QuizItem(
                id=quiz_id, user_id=uid, note_id=note_id, card_id=card_id,
                question="q", answer="a", question_type=QuestionType.short_answer,
            ))
            await db.commit()

        async with test_db() as db:
            result = await review_service.submit_answer(quiz_id, uid, "随便写", 0, db)

        resp = SubmitAnswerResponse.from_service_result(result)
        assert resp.grading_detail is None
        assert resp.needs_self_assessment is True
