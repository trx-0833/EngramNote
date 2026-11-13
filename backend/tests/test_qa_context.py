"""
多轮上下文组装与检索查询拼接（docs/qa-history-plan.md §4 / §8.1 / §8.2）

## 为什么要这么细地测这一段

多轮回答"看起来能接上"太容易了：随便把前几轮拼进去，模型也会顺着说。
但真正会坏的是**边界**：预算裁到最后剩几轮、截断点落在哪里、
失败与中止的轮次有没有漏进去、system 里那条优先级指令有没有被后来的重构删掉。
这些都不会在"人工聊两句"里暴露，只会在长会话里慢慢变成"AI 记性很差/说错还继续错"。

所以本文件断言的是**结构**，不是语义：`messages` 的角色顺序、条数、
每条的字符数、system 里必须出现那句话。
"""

from __future__ import annotations

from app.services.qa_context import (
    MULTITURN_SYSTEM_RULE,
    assemble_messages,
    build_retrieval_query,
    context_turns_used,
    usable_history,
)

SYSTEM = "你是一个学习助手。"
CURRENT = "参考资料：\n[片段]\n\n问题：那它对寿命有影响吗？"


def _roles(messages) -> list[str]:
    return [m["role"] for m in messages]


class TestAssembleMessages:
    def test_first_turn_is_identical_to_before(self):
        """★ 首轮必须与改造前逐字一致：没有历史就不该插入任何额外内容"""
        messages = assemble_messages(system_prompt=SYSTEM, current_user_content=CURRENT)
        assert messages == [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": CURRENT},
        ]
        assert MULTITURN_SYSTEM_RULE not in messages[0]["content"]

    def test_history_goes_between_system_and_current_question(self):
        history = [("浮充和均充的区别？", "浮充长期恒压，均充短时升压。")]
        messages = assemble_messages(system_prompt=SYSTEM, current_user_content=CURRENT, history=history)

        assert _roles(messages) == ["system", "user", "assistant", "user"]
        assert messages[1]["content"] == "浮充和均充的区别？"
        assert messages[2]["content"] == "浮充长期恒压，均充短时升压。"
        assert messages[-1]["content"] == CURRENT

    def test_system_gets_priority_rule_only_when_history_present(self):
        """★ 那句话是抑制"沿着错误继续推"的唯一手段，不能被删"""
        with_history = assemble_messages(system_prompt=SYSTEM, current_user_content=CURRENT, history=[("q", "a")])
        assert MULTITURN_SYSTEM_RULE in with_history[0]["content"]
        assert with_history[0]["content"].startswith(SYSTEM)

    def test_only_last_n_turns_are_kept(self):
        history = [(f"q{i}", f"a{i}") for i in range(10)]
        messages = assemble_messages(system_prompt=SYSTEM, current_user_content=CURRENT, history=history, max_turns=3)
        # 3 轮 = 6 条历史消息 + system + 当前问
        assert len(messages) == 1 + 6 + 1
        assert messages[1]["content"] == "q7"  # 从最旧的 q7 开始（保留最近 3 轮）
        assert messages[5]["content"] == "q9"  # 最后一条历史问题（紧接着当前问之前）

    def test_long_answer_is_clipped_with_marker(self):
        history = [("q", "答" * 1000)]
        messages = assemble_messages(
            system_prompt=SYSTEM,
            current_user_content=CURRENT,
            history=history,
            answer_chars=50,
        )
        answer = messages[2]["content"]
        assert len(answer) <= 50 + len("…（略）")
        assert answer.endswith("…（略）")

    def test_total_budget_drops_oldest_first(self):
        """★ 超出总预算时从**最旧**开始丢：越近的上下文越可能相关"""
        history = [(f"问题{i}" * 20, f"答案{i}" * 20) for i in range(4)]
        messages = assemble_messages(
            system_prompt=SYSTEM,
            current_user_content=CURRENT,
            history=history,
            max_turns=10,
            answer_chars=10_000,
            max_chars=200,
        )
        kept_questions = [m["content"] for m in messages if m["role"] == "user"][:-1]  # 去掉当前问
        assert kept_questions, "预算太小也不该把历史全丢掉以外的行为变化"
        assert kept_questions[-1].startswith("问题3"), "最近的一轮必须留下"
        assert all(not q.startswith("问题0") for q in kept_questions), "最旧的一轮必须被丢掉"

    def test_context_turns_used_matches_assembled_history(self):
        history = [(f"q{i}", "a" * 100) for i in range(5)]
        used = context_turns_used(history=history, max_turns=2, answer_chars=1000, max_chars=100000)
        messages = assemble_messages(
            system_prompt=SYSTEM,
            current_user_content=CURRENT,
            history=history,
            max_turns=2,
            answer_chars=1000,
            max_chars=100000,
        )
        assert used == 2
        assert len([m for m in messages if m["role"] == "assistant"]) == used


class TestUsableHistory:
    def test_failed_and_aborted_turns_are_excluded(self):
        """★ 失败没有答案、中止只有半截 —— 都不能进上下文（计划 §4.4）"""
        turns = [
            ("q1", "答1", "ok"),
            ("q2", "", "failed"),
            ("q3", "答到一半", "aborted"),
            ("q4", "答4", "ok"),
        ]
        assert usable_history(turns) == [("q1", "答1"), ("q4", "答4")]

    def test_ok_turn_with_blank_answer_is_excluded(self):
        assert usable_history([("q", "   ", "ok")]) == []


class TestRetrievalQuery:
    def test_first_turn_uses_question_as_is(self):
        assert build_retrieval_query(None, "浮充是什么？") == "浮充是什么？"

    def test_followup_prepends_previous_question(self):
        """★ "那它的寿命呢"必须带上主语才检索得到"""
        assert build_retrieval_query("浮充和均充的区别？", "那它对寿命有影响吗？") == (
            "浮充和均充的区别？ 那它对寿命有影响吗？"
        )

    def test_previous_question_is_clipped(self):
        prev = "旧" * 500
        query = build_retrieval_query(prev, "那它呢")
        assert query.startswith("旧" * 120)
        assert len(query) < 200

    def test_same_question_is_not_duplicated(self):
        assert build_retrieval_query("浮充是什么？", "浮充是什么？") == "浮充是什么？"
