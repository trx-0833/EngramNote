# -*- coding: utf-8 -*-
"""A4 LLM 打分 / B5 笔记评估的**纯函数**测试（不需要 LLM）

## 为什么只测纯函数

这两个服务的价值在提示词与解析，而**提示词没法用测试保证质量**
（那要靠实跑出来的报告）。能用测试钉住的是解析层 —— 而解析层恰恰是最容易
静默出错的地方：

- 模型返回带代码块围栏的 JSON（它经常无视「不要代码块」的要求）；
- 模型少给一个维度、或给个 `85分` 这种字符串；
- 模型编一个不在输入里的 `card_id`；
- 模型把分数写成 `null`。

这些情况下**绝不能默认成 0 分** —— 0 分的语义是「这张卡毫无价值」，
而「解析不出来」是**我们这边**的问题。把后者写成前者会污染报告、
并可能诱导删除（而删除是要单独批的破坏性动作）。
"""

from __future__ import annotations

import pytest

from app.services.card_quality_llm_service import (
    CardScore,
    _coerce_int,
    parse_batch_response,
)
from app.services.note_eval_service import (
    SEGMENT_CHARS,
    parse_eval_response,
    split_source,
)

# ---------------------------------------------------------------- A4 解析


class TestCoerceInt:
    """分数收窄：合法就收，不合法返回 None（**不是 0**）"""

    def test_accepts_int_and_float(self):
        assert _coerce_int(85) == 85
        assert _coerce_int(85.7) == 85

    def test_accepts_numeric_string(self):
        # 模型经常把分数写成字符串
        assert _coerce_int("85") == 85
        assert _coerce_int(" 85 ") == 85
        assert _coerce_int("85.0") == 85

    def test_clamps_out_of_range(self):
        assert _coerce_int(150) == 100
        assert _coerce_int(-20) == 0

    def test_returns_none_not_zero_for_unparsable(self):
        """★ 这是本文件最重要的一条

        返回 0 会让「解析失败」看起来像「质量极差」—— 而两者在报告里
        完全无法区分，且 0 分会进低分清单、可能诱导删除。
        """
        for bad in (None, "", "   ", "85分", "abc", [], {}, True, False):
            assert _coerce_int(bad) is None, f"{bad!r} 应当返回 None"

    def test_bool_is_explicitly_excluded(self):
        """`True` 是 `int` 的子类，不排除的话会被收成 1 分"""
        assert _coerce_int(True) is None


class TestParseBatchResponse:
    @staticmethod
    def _ids():
        return ["c1", "c2"]

    @staticmethod
    def _good():
        return (
            '{"results": ['
            '{"card_id": "c1", "info_score": 90, "self_contained_score": 80,'
            ' "quizzable_score": 70, "reason": "好卡", "suggested_action": "keep"},'
            '{"card_id": "c2", "info_score": 20, "self_contained_score": 30,'
            ' "quizzable_score": 10, "reason": "太短", "suggested_action": "rewrite"}'
            "]}"
        )

    def test_parses_normally(self):
        out = parse_batch_response(self._good(), self._ids(), 0)
        assert [s.card_id for s in out] == ["c1", "c2"]
        assert out[0].average == 80.0
        assert out[1].suggested_action == "rewrite"

    def test_strips_markdown_fence(self):
        """★ 模型经常无视「不要代码块」的要求 —— 这不该让整批作废"""
        raw = "```json\n" + self._good() + "\n```"
        out = parse_batch_response(raw, self._ids(), 0)
        assert len(out) == 2, "带围栏的响应必须也能解析"

    def test_accepts_bare_array(self):
        raw = (
            '[{"card_id": "c1", "info_score": 90, "self_contained_score": 80,'
            ' "quizzable_score": 70, "reason": "x", "suggested_action": "keep"}]'
        )
        out = parse_batch_response(raw, self._ids(), 0)
        assert len(out) == 1

    def test_drops_fabricated_card_id(self):
        """★ 模型偶尔会编 id 或把上一批的 id 带过来 —— 认了就会张冠李戴"""
        raw = (
            '{"results": ['
            '{"card_id": "c1", "info_score": 90, "self_contained_score": 80,'
            ' "quizzable_score": 70, "reason": "x", "suggested_action": "keep"},'
            '{"card_id": "NOT_IN_INPUT", "info_score": 10, "self_contained_score": 10,'
            ' "quizzable_score": 10, "reason": "y", "suggested_action": "delete_candidate"}'
            "]}"
        )
        out = parse_batch_response(raw, self._ids(), 0)
        assert [s.card_id for s in out] == ["c1"]

    def test_skips_card_missing_a_dimension(self):
        """★ 缺一个维度的卡被跳过，而不是补 0"""
        raw = (
            '{"results": [{"card_id": "c1", "info_score": 90,'
            ' "self_contained_score": 80, "reason": "x", "suggested_action": "keep"}]}'
        )
        out = parse_batch_response(raw, self._ids(), 0)
        assert out == [], "三个维度不全的卡不能算有分"

    def test_invalid_action_falls_back_to_keep(self):
        raw = (
            '{"results": [{"card_id": "c1", "info_score": 90, "self_contained_score": 80,'
            ' "quizzable_score": 70, "reason": "x", "suggested_action": "删了吧"}]}'
        )
        out = parse_batch_response(raw, self._ids(), 0)
        assert out[0].suggested_action == "keep", "无法统计的自由文本要回落成 keep"

    def test_bad_json_returns_empty_not_raise(self):
        assert parse_batch_response("这不是 JSON", self._ids(), 0) == []
        assert parse_batch_response("", self._ids(), 0) == []

    def test_unknown_shape_returns_empty(self):
        assert parse_batch_response('{"foo": 1}', self._ids(), 0) == []

    def test_average_is_mean_of_three(self):
        s = CardScore("c1", 90, 60, 30, "x", "keep")
        assert s.average == 60.0


# ---------------------------------------------------------------- B5 分段


class TestSplitSource:
    def test_short_text_passes_through(self):
        assert split_source("一段话") == ["一段话"]

    def test_empty_returns_empty(self):
        assert split_source("") == []
        assert split_source("   \n\n  ") == []

    def test_splits_by_paragraph_within_limit(self):
        text = "\n\n".join("x" * 100 for _ in range(50))
        segs = split_source(text, limit=500)
        assert len(segs) > 1
        assert all(len(s) <= 500 for s in segs), "每段都不得超过上限"

    def test_hard_splits_oversized_paragraph(self):
        """★ 否则那一段永远发不出去 —— 会被静默丢弃，让「遗漏」凭空变多"""
        text = "y" * 1200
        segs = split_source(text, limit=500)
        assert len(segs) >= 3
        assert "".join(segs) == text, "硬切不得丢字符"

    def test_no_paragraph_lost(self):
        paras = [f"段落{i}" + "z" * 80 for i in range(20)]
        segs = split_source("\n\n".join(paras), limit=300)
        joined = "\n\n".join(segs)
        for para in paras:
            assert para in joined, f"{para[:10]} 丢了"

    def test_default_threshold_is_conservative(self):
        assert 5000 <= SEGMENT_CHARS <= 20000


class TestParseEvalResponse:
    def test_parses_normally(self):
        raw = (
            '{"missing_concepts": [{"name": "开断容量", "why_important": "选型依据",'
            ' "source_quote": "开断容量应大于"}],'
            ' "misunderstandings": [{"card_id": "c1", "card_title": "T", "problem": "丢了条件",'
            ' "source_quote": "在 X 情况下", "suggestion": "补上"}],'
            ' "coverage_note": "覆盖良好"}'
        )
        d = parse_eval_response(raw)
        assert d["missing_concepts"][0]["name"] == "开断容量"
        assert d["misunderstandings"][0]["card_id"] == "c1"

    def test_strips_markdown_fence(self):
        raw = '```json\n{"missing_concepts": [], "misunderstandings": []}\n```'
        d = parse_eval_response(raw)
        assert d["missing_concepts"] == []

    def test_bad_json_returns_empty_structure(self):
        d = parse_eval_response("不是 JSON")
        assert d == {"missing_concepts": [], "misunderstandings": [], "coverage_note": ""}

    def test_top_level_array_returns_empty_structure(self):
        d = parse_eval_response("[1, 2, 3]")
        assert d["missing_concepts"] == []


# ---------------------------------------------------------------- 端到端（替身网关）


class _FakeGateway:
    """只记录调用、返回预设内容的替身（**不发网络请求**）"""

    def __init__(self, content: str, *, cached: bool = False):
        self.content = content
        self.cached = cached
        self.calls: list[dict] = []

    async def chat_detailed(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return {
            "content": self.content,
            "usage": {"total_tokens": 123, "cached": self.cached},
        }


class TestEvaluateNoteNoSource:
    """★ 没有原文时**不得发调用** —— 否则模型会凭卡片自己编「遗漏」"""

    @pytest.mark.asyncio
    async def test_no_source_means_no_call(self):
        from app.services.note_eval_service import evaluate_note

        gw = _FakeGateway("{}")
        res = await evaluate_note(source_text=None, cards=[{"id": "c1"}], gateway=gw)
        assert res.source_unavailable is True
        assert gw.calls == [], "没有原文就不该发调用（发了就是花钱编内容）"
        assert res.calls == 0

    @pytest.mark.asyncio
    async def test_blank_source_means_no_call(self):
        from app.services.note_eval_service import evaluate_note

        gw = _FakeGateway("{}")
        res = await evaluate_note(source_text="   \n  ", cards=[], gateway=gw)
        assert res.source_unavailable is True
        assert gw.calls == []


class TestEvaluateNoteWithSource:
    @pytest.mark.asyncio
    async def test_single_segment_one_call(self):
        from app.services.note_eval_service import evaluate_note

        gw = _FakeGateway(
            '{"missing_concepts": [{"name": "N", "why_important": "w", "source_quote": "q"}],'
            ' "misunderstandings": [], "coverage_note": "ok"}'
        )
        res = await evaluate_note(source_text="一段原文", cards=[{"id": "c1"}], gateway=gw)
        assert len(gw.calls) == 1
        assert res.segments == 1
        assert res.missing_concepts[0].name == "N"
        assert res.coverage_note == "ok"
        assert res.total_tokens == 123

    @pytest.mark.asyncio
    async def test_multi_segment_calls_per_segment(self):
        from app.services.note_eval_service import evaluate_note

        gw = _FakeGateway('{"missing_concepts": [{"name": "N"}], "misunderstandings": []}')
        long_text = "\n\n".join("x" * 200 for _ in range(100))
        res = await evaluate_note(source_text=long_text, cards=[], gateway=gw)
        assert res.segments > 1
        assert len(gw.calls) == res.segments, "每段一次调用"
        assert len(res.missing_concepts) == res.segments

    @pytest.mark.asyncio
    async def test_cached_flag_reflects_quota_use(self):
        """★ 前端要靠它明示「本次未消耗额度」，判错会误导用户"""
        from app.services.note_eval_service import evaluate_note

        hit = _FakeGateway("{}", cached=True)
        res = await evaluate_note(source_text="x", cards=[], gateway=hit)
        assert res.all_cached is True

        miss = _FakeGateway("{}", cached=False)
        res2 = await evaluate_note(source_text="x", cards=[], gateway=miss)
        assert res2.all_cached is False

    @pytest.mark.asyncio
    async def test_call_failure_does_not_raise(self):
        from app.services.note_eval_service import evaluate_note

        class _Boom:
            async def chat_detailed(self, *a, **k):
                raise RuntimeError("网关炸了")

        res = await evaluate_note(source_text="x", cards=[], gateway=_Boom())
        assert res.calls == 0
        assert res.missing_concepts == []
