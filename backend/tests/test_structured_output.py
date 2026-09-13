"""结构化输出校验测试（阶段 4.1 原文的一条：Pydantic 校验）

## 这份测试要证明什么

校验层的价值全在**它怎么处理坏数据**，而不是"好数据能通过"。三类会骗人的做法：

| 骗法 | 后果 | 对应测试 |
|---|---|---|
| 静默强制转换（改前的做法） | "模型在乱返回"永远不可见 | `test_coercion_is_recorded_not_hidden` |
| 一条坏就整批丢弃 | 29 条好的陪 1 条坏的殉葬，重试还要再付一次全额费用 | `test_one_bad_item_does_not_discard_the_batch` |
| 把"修正"和"拒绝"混成一个数 | 看不出"模型开始在枚举上胡说"这个前兆 | `test_rejected_and_coerced_are_counted_separately` |

另外两条边界：**判分结果不适用"修正"**（猜一个 verdict 会改变复习间隔，
且间隔一旦写进调度就无法事后纠正）、**校验层永不抛异常**。
"""

import pytest

from app.services.llm.structured import (
    CardPoint,
    ExtensionPoint,
    GradeResult,
    QuizQuestion,
    validate_items,
)


class TestCardPoint:
    def test_valid_item_passes_untouched(self):
        out = validate_items(
            [{"card_type": "formula", "title": "浮充", "content": "内容",
              "source_text": "原文", "is_key_point": True}],
            CardPoint,
        )
        assert out.rejected_count == 0 and out.coerced_count == 0
        card = out.valid[0]
        assert (card.card_type, card.title, card.source_text) == ("formula", "浮充", "原文")
        assert card.is_key_point is True

    def test_coercion_is_recorded_not_hidden(self):
        """★ 枚举值不认识 → **修正并记录**（而不是悄悄当 concept）

        改造前 `try: CardType(x) except ValueError: concept` 把这件事彻底吞掉：
        用户看到的是一堆概念卡，没人知道其中一部分本该是别的类型；
        而"模型开始在 card_type 上胡说"正是提示词需要调整的前兆。
        """
        out = validate_items(
            [{"card_type": "formula_extra", "title": "均充", "content": "内容"}],
            CardPoint,
        )
        assert len(out.valid) == 1, "枚举不认识不该让条目不可用"
        assert out.valid[0].card_type == "concept"
        assert out.coerced_count == 1
        assert out.rejected_count == 0
        assert "formula_extra" in out.issues[0].reason

    def test_missing_required_text_is_rejected(self):
        out = validate_items(
            [
                {"title": "有标题", "content": "有内容"},
                {"title": "", "content": "无标题"},
                {"title": "无内容", "content": "   "},
                {"title": 123, "content": "标题不是字符串"},
                {"content": "压根没有标题字段"},
            ],
            CardPoint,
        )
        assert len(out.valid) == 1
        assert out.rejected_count == 4
        assert all(i.action == "rejected" for i in out.issues)

    def test_unknown_fields_are_ignored(self):
        """多余字段忽略而非报错：模型多给一个 `confidence` 不该让卡片作废"""
        out = validate_items(
            [{"title": "T", "content": "C", "confidence": 0.9, "extra": {"a": 1}}],
            CardPoint,
        )
        assert len(out.valid) == 1
        assert "confidence" not in out.valid[0].model_dump()

    def test_coercion_field_is_not_serialized(self):
        """`coercion_notes` 是内部记账，不得混进 `model_dump()` 写库"""
        out = validate_items([{"card_type": "bad", "title": "T", "content": "C"}], CardPoint)
        assert "coercion_notes" not in out.valid[0].model_dump()


class TestQuizQuestion:
    def test_options_string_is_normalized_to_list(self):
        """模型偶尔把 options 写成 JSON 字符串 —— 规范成列表（改造前是原样 json.dumps）"""
        out = validate_items(
            [{"question": "Q", "answer": "A", "question_type": "choice",
              "options": '["A. 1", "B. 2"]'}],
            QuizQuestion,
        )
        assert out.valid[0].options == ["A. 1", "B. 2"]

    def test_non_json_options_are_emptied_and_reported(self):
        out = validate_items(
            [{"question": "Q", "answer": "A", "options": "不是 JSON"}],
            QuizQuestion,
        )
        assert out.valid[0].options is None
        assert out.coerced_count == 1

    def test_enum_fields_are_closed_sets(self):
        out = validate_items(
            [{"question": "Q", "answer": "A", "question_type": "essay", "difficulty": "insane"}],
            QuizQuestion,
        )
        assert out.valid[0].question_type == "choice"
        assert out.valid[0].difficulty == "medium"
        assert out.coerced_count == 2

    def test_missing_answer_is_rejected(self):
        out = validate_items([{"question": "Q", "question_type": "choice"}], QuizQuestion)
        assert out.valid == []
        assert out.rejected_count == 1


@pytest.mark.asyncio
class TestBatchSemantics:
    async def test_one_bad_item_does_not_discard_the_batch(self):
        """★ 逐条校验：29 条好的不陪 1 条坏的殉葬

        整批丢弃的代价不只是"少 29 条"，而是**重试一次要再付一次全额费用** ——
        成本治理刚做完，不该在校验层破功。
        """
        items = [{"title": f"知识点{i}", "content": f"内容{i}"} for i in range(29)]
        items.insert(13, {"title": "", "content": "坏的"})
        out = validate_items(items, CardPoint)
        assert len(out.valid) == 29
        assert out.rejected_count == 1
        assert out.total == 30

    async def test_rejected_and_coerced_are_counted_separately(self):
        """★ 两类处置必须分开计数（否则看不出"模型在枚举上胡说"这个前兆）"""
        out = validate_items(
            [
                {"title": "好", "content": "内容"},
                {"title": "枚举坏了", "content": "内容", "card_type": "???"},
                {"title": "", "content": "字段缺了"},
            ],
            CardPoint,
        )
        assert (len(out.valid), out.rejected_count, out.coerced_count) == (2, 1, 1)
        assert "2/3 条可用" in out.summary()
        assert "拒绝 1" in out.summary() and "修正 1" in out.summary()


class TestRobustness:
    def test_non_list_input_is_rejected_not_crashed(self):
        for raw in (None, {}, "字符串", 42):
            out = validate_items(raw, CardPoint)
            assert out.valid == []
            assert out.rejected_count == 1

    def test_non_dict_items_are_rejected(self):
        out = validate_items([{"title": "T", "content": "C"}, "字符串", 42, None], CardPoint)
        assert len(out.valid) == 1
        assert out.rejected_count == 3

    def test_empty_input(self):
        out = validate_items([], CardPoint)
        assert out.valid == [] and out.issues == [] and out.total == 0

    def test_extension_point_is_supported(self):
        out = validate_items([{"card_type": "definition", "title": "T", "content": "C"}],
                             ExtensionPoint)
        assert len(out.valid) == 1


class TestGradeResultIsSpecial:
    """★ 判分是唯一"宁可丢弃也不修正"的场景"""

    def test_unknown_verdict_is_rejected_not_coerced(self):
        """猜一个判分结论会直接改变复习间隔，而间隔写进调度后无法事后纠正

        （对比 `CardPoint`：枚举坏了仍可用并记录 —— 卡片类型错了只是分类不准，
        判分错了则会让"答错"的题被排到很久之后。）
        """
        out = validate_items([{"verdict": "maybe", "confidence": 0.9}], GradeResult)
        assert out.valid == []
        assert out.rejected_count == 1
        assert out.coerced_count == 0, "判分结论不允许被'修正'"

    def test_confidence_is_clamped(self):
        """模型偶尔给 1.5 或 -0.2 —— 裁剪而不是丢弃（钳制不会改变结论）"""
        hi = validate_items([{"verdict": "correct", "confidence": 1.5}], GradeResult)
        lo = validate_items([{"verdict": "correct", "confidence": -0.2}], GradeResult)
        assert hi.valid[0].confidence == 1.0
        assert lo.valid[0].confidence == 0.0

    def test_valid_grade_passes(self):
        out = validate_items(
            [{"verdict": "partial", "missing_points": ["A"], "misconceptions": [],
              "confidence": 0.7, "reason": "缺一点"}],
            GradeResult,
        )
        assert out.valid[0].verdict == "partial"
        assert out.valid[0].missing_points == ["A"]


class TestWiring:
    """校验层必须真的接在**每一条** LLM 输出入库路径上

    只建模型不接线，等于给模型定义了一堆没人用的约束 —— 而"某条路径漏了"
    是这类改动最典型的失败方式（4.8/4.9 的入库门当时就只覆盖了理解管道）。
    """

    def _src(self, rel: str) -> str:
        """读源码并**剥掉注释**后再断言

        ⚠️ 教训（附录 AI.5 同一类）：解释"这里以前会兜底成 X"的注释本身包含 X，
        直接查整份文件会让文档触发断言。静态检查必须对准代码。
        """
        import io

        src = io.open(rel, encoding="utf-8").read()
        return "\n".join(line.split("#", 1)[0] for line in src.splitlines())

    def test_card_intake_validates_before_the_gate(self):
        src = self._src("app/services/card_intake_service.py")
        assert "validate_items(knowledge_points, CardPoint" in src
        assert "for item in validation.valid" in src
        assert "outcome.coerced" in src, "被修正的条目没有被上报"

    def test_quiz_path_validates_before_insert(self):
        src = self._src("app/tasks/understand_tasks.py")
        assert "validate_items(questions, QuizQuestion" in src
        # 旧的静默强制转换必须消失，否则两套逻辑会并存并互相矛盾
        assert "q_type_str" not in src, "旧的 question_type 静默转换还在"
        assert "except ValueError:\n                        difficulty = DifficultyLevel.medium" not in src

    def test_combined_analysis_path_validates(self):
        """联合分析（资料 + 用户笔记）这条路此前**完全没有校验**"""
        src = self._src("app/services/knowledge_link_service.py")
        assert "validate_items(raw_points, CardPoint" in src

    def test_extension_path_validates_and_drops_unnamed(self):
        """拓展生成：缺标题不再被兜底成"未命名拓展知识点"（那会掩盖模型异常）"""
        src = self._src("app/services/knowledge_link_service.py")
        assert "validate_items(extensions, ExtensionPoint" in src
        assert "未命名拓展知识点" not in src, (
            "旧的兜底标题还在 —— 它会把'模型没按格式返回'变成一堆同名卡片"
        )

    def test_relation_path_validates_and_reports(self):
        """关系推断：不合法的条目仍然丢弃，但必须**被报告**（改前完全静默）"""
        src = self._src("app/services/graph_service.py")
        assert "validate_items(relations, CardRelationPoint" in src
        assert "valid_relation_types" not in src, "旧的手写枚举检查仍在（两套判定会漂移）"

    def test_relation_with_unknown_type_is_rejected(self):
        """关系类型认不出来 → 丢弃（造一条错误的边会被用户当成事实）"""
        from app.services.llm.structured import CardRelationPoint

        out = validate_items(
            [{"card_id_a": "c1", "card_id_b": "c2", "relation_type": "similar_to"}],
            CardRelationPoint,
        )
        assert out.valid == []
        assert out.rejected_count == 1

    def test_grading_path_uses_the_shared_model(self):
        """判分路径必须用 `GradeResult`，而不是自己再写一套 verdict 判定

        两套判定的后果是漂移：改了一处、忘另一处，于是"什么算合法 verdict"
        在解析层和校验层给出不同答案。
        """
        src = self._src("app/services/llm/scenes.py")
        assert "validate_items([result], GradeResult" in src
        assert 'verdict not in ("correct", "partial", "incorrect")' not in src
        assert "_str_list" not in src, "旧的手写规范化还在（与 GradeResult 重复）"
