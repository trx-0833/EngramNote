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
