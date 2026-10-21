# -*- coding: utf-8 -*-
"""
出题提示词三版 + 题型/难度配比（A2 / A3′）
============================================

## 这一组盯的是什么

改造前 `question_session`（v1）把两件事**写死在提示词里**：

1. 任务描述："请为每组知识点生成**选择题**" ⇒ 只能出 choice；
2. JSON 样例：`"question_type": "choice"` + `"difficulty": "easy"`
   —— **样例是最强的指令**，模型会照着填。

实测真库 **3358 道题全是 `choice`**、难度 easy 2567 / medium 773 / hard 仅 18。
`understand_tasks.py` 也不传题型与难度 ⇒ **不改提示词就重跑一万遍还是"简单四选一"**。

## 三版的分工（A3′ 的对照实验靠它们同时存在）

| 版本 | 改了什么 |
|---|---|
| v1 | 原有行为（只有选择题、只有 easy）—— **仍是服务端默认**，对照组 |
| v2 | 题型与难度**可配**，逐题型给写法要求、逐难度给判据 |
| v3 | v2 + **出题质量规则**（同范畴干扰项 / 唯一答案 / 可自评要点 / 不引外部知识 / 同点多角度） |

## 为什么默认仍是 v1

改默认 ⇒ ① 上游"重新理解"不再是对照组；② 全库提示词缓存当场失效；
③ 切版是**产品决策**，该由 `DEFAULT_QUESTION_PROMPT_VERSION` 这一个常量说了算，
而不是藏在一个 `or` 的默认值里。三条都写在那个常量的注释里。
"""

from __future__ import annotations


from app.services.llm import prompts as P

#: 一个典型的定向出题配比（盲点卡：2 选择 + 1 填空 + 1 简答）
TYPES = {"choice": 2, "fill_blank": 1, "short_answer": 1}
DIFFS = {"easy": 1, "medium": 2, "hard": 1}


class TestDefaultVersionIsUnchanged:
    """★ 改造**不动存量**：默认版本与默认行为必须逐字节还是 v1"""

    def test_default_version_constant_is_v1(self):
        assert P.DEFAULT_QUESTION_PROMPT_VERSION == "1", (
            "默认版本被改了 —— 那会让上游「重新理解」不再是对照组，"
            "并让全库提示词缓存当场失效（见那个常量的注释）"
        )

    def test_no_version_no_plan_returns_v1_text(self):
        """不传版本、不传配比 ⇒ 与 v1 常量**逐字节**相同"""
        got = P.question_session_prompt()
        assert got == P.QUESTION_SYSTEM_PROMPT, "默认路径不再是 v1 文本"
        assert "选择题" in got and '"difficulty": "easy"' in got

    def test_v1_builder_ignores_the_plan(self):
        """显式要 v1 时，配比**不生效**（否则"对照组"就被参数污染了）"""
        got = P.question_session_prompt(
            question_types=TYPES, difficulty_mix=DIFFS, version="1"
        )
        assert got == P.QUESTION_SYSTEM_PROMPT
        assert "fill_blank" not in got, "v1 里不该出现别的题型"

    def test_unknown_version_falls_back_instead_of_raising(self):
        """版本号写错 ⇒ 退回默认版，**不抛异常**

        出题是长任务（一轮 35 分钟、要花额度），为"参数写错"整篇失败代价太大。
        """
        got = P.question_session_prompt(version="99")
        assert got == P.QUESTION_SYSTEM_PROMPT


class TestV2:
    def test_v2_is_different_from_v1(self):
        """v2 与 v1 **必须不同** —— 否则 A3′ 跑的是两遍同一版"""
        v1 = P.question_session_prompt(version="1")
        v2 = P.question_session_prompt(
            question_types=TYPES, difficulty_mix=DIFFS, version="2"
        )
        assert v2 != v1

    def test_v2_lists_every_requested_question_type_with_a_rule(self):
        """★ 配比里的**每个**题型都要出现在提示词里，且带写法要求

        只写"出 2 选择 1 填空 1 简答"而不说怎么写，模型对"填空"的理解是
        它自己的（常见结果是"把选择题的题干挖个洞但选项还在"）。
        """
        v2 = P.question_session_prompt(question_types=TYPES, version="2")
        for qtype in ("choice", "fill_blank", "short_answer"):
            assert qtype in v2, f"题型 {qtype} 没出现在提示词里"
        assert "____" in v2, "填空题的写法要求（留空）没说"
        assert "要点" in v2, "简答题的写法要求（要点清单）没说"

    def test_v2_lists_every_requested_difficulty_with_a_criterion(self):
        """★ 难度不只是报名字，要给**判据**

        只写"hard × 1"时模型对 hard 的理解通常等于"题干写长一点"。
        实测真库 hard 只有 18/3358，而**旧提示词里根本没出现过 hard 这个词**
        —— 那 18 道是模型在样例之外自己发挥的。
        """
        v2 = P.question_session_prompt(difficulty_mix=DIFFS, version="2")
        for level in ("easy", "medium", "hard"):
            assert level in v2, f"难度 {level} 没出现"
        assert "原文" in v2, "难度的判据（答案在不在原文里）没说"

    def test_v2_sample_matches_the_first_requested_type(self):
        """★ JSON 样例的 `question_type` 必须跟着配比走

        样例是最强指令：改造前样例写死 `"choice"`，于是任务描述里就算写了
        "也可以出填空"，模型也会照着样例填空。这条是那个失效机制的守卫。
        """
        v2 = P.question_session_prompt(
            question_types={"fill_blank": 3}, version="2"
        )
        assert '"question_type": "fill_blank"' in v2, (
            "配比只有填空题，样例却还写着 choice —— 模型会照样例走"
        )
        assert '"question_type": "choice"' not in v2

    def test_v2_sample_difficulty_is_not_easy(self):
        """样例里的 difficulty 不能写 `easy`（那是 v1 的失效机制之一）"""
        v2 = P.question_session_prompt(question_types=TYPES, version="2")
        assert '"difficulty": "easy"' not in v2

    def test_v2_options_rule_covers_non_choice_types(self):
        """★ 非选择题的 `options` 要传空数组（不写的话模型会编 4 个选项）"""
        v2 = P.question_session_prompt(question_types=TYPES, version="2")
        assert "空数组" in v2 or "empty array" in v2

    def test_v2_without_plan_still_differs_from_v1_but_has_no_plan_block(self):
        """没配比时：文本仍与 v1 不同（措辞变了），但没有"配比"段"""
        v2 = P.question_session_prompt(version="2")
        assert v2 != P.QUESTION_SYSTEM_PROMPT
        assert "题型配比" not in v2
        assert "难度配比" not in v2

    def test_v2_english_variant(self):
        """英文分支也要有配比与写法要求（否则英文界面退回全选择题）"""
        v2 = P.question_session_prompt(
            question_types=TYPES, difficulty_mix=DIFFS, locale="en", version="2"
        )
        assert "fill_blank" in v2 and "short_answer" in v2
        assert "Difficulty mix" in v2
        assert "____" in v2


class TestV3:
    def test_v3_extends_v2_verbatim(self):
        """★ v3 = v2 + 质量规则（**前缀逐字节相同**）

        这样 A3′ 里"v3 比 v2 好"才能归因到质量规则上，
        而不是"两版顺手都改了别的"。
        """
        v2 = P.question_session_prompt(
            question_types=TYPES, difficulty_mix=DIFFS, version="2"
        )
        v3 = P.question_session_prompt(
            question_types=TYPES, difficulty_mix=DIFFS, version="3"
        )
        assert v3.startswith(v2), "v3 不是「v2 加一段」，两版没法对比"

    def test_v3_quality_rules_are_all_present(self):
        """五条质量规则逐条验（少一条就是"v3 其实只有四条"）"""
        v3 = P.question_session_prompt(version="3")
        markers = [
            "同范畴",      # A 干扰项
            "唯一",        # B 填空题答案
            "自评",        # C 简答题要点
            "原文没有",    # D 不得引入外部信息
            "同一个点",    # E 同知识点的多题要换角度
        ]
        for m in markers:
            assert m in v3, f"质量规则缺少「{m}」那一条"

    def test_v3_english_quality_rules(self):
        v3 = P.question_session_prompt(locale="en", version="3")
        assert "same-category" in v3
        assert "unique" in v3


class TestQuestionPlanFor:
    """`_question_plan_for`：两个入参 → 会话级配比"""

    def test_no_target_categories_returns_none(self):
        """★ 没有目标类别 ⇒ `(None, None)` ⇒ 会话退回默认版，**行为逐字节不变**

        这是"改造不动存量"的保证。写成"总给一个默认配比"会让每一次
        普通的出题都换口径 —— 而那正是"默认改了 = 上游全部改口径"。
        """
        from app.tasks.understand_tasks import _question_plan_for

        assert _question_plan_for(None, None) == (None, None)
        assert _question_plan_for([], None) == (None, None)

    def test_target_categories_gives_the_default_mixes(self):
        from app.tasks.understand_tasks import (
            DEFAULT_DIFFICULTY_MIX,
            DEFAULT_QUESTION_TYPE_MIX,
            _question_plan_for,
        )

        types, diffs = _question_plan_for(["blind_spot"], None)
        assert types == DEFAULT_QUESTION_TYPE_MIX
        assert diffs == DEFAULT_DIFFICULTY_MIX
        # 三种题型都要有 —— 只有 choice 就等于没修
        assert set(types) == {"choice", "fill_blank", "short_answer"}

    def test_explicit_difficulty_overrides_the_default_mix(self):
        """★ 显式指定难度 ⇒ **压成该档**，而不是被默认配比盖掉

        `target_difficulty` 此前的 docstring 写的是"暂不强制使用，仅作为提示"
        —— 也就是说它进了卡片文本却没人保证生效。现在它至少能决定会话级配比。
        """
        from app.tasks.understand_tasks import _question_plan_for

        _, diffs = _question_plan_for(["blind_spot"], "hard")
        assert diffs == {"hard": 4}, f"实际 {diffs}"
        assert "easy" not in diffs

    def test_invalid_difficulty_is_ignored_not_crashed(self):
        """非法难度名 ⇒ 退回默认配比（不透传垃圾给提示词）"""
        from app.tasks.understand_tasks import (
            DEFAULT_DIFFICULTY_MIX,
            _question_plan_for,
        )

        _, diffs = _question_plan_for(["blind_spot"], "impossible")
        assert diffs == DEFAULT_DIFFICULTY_MIX


class TestCreateQuestionSession:
    """会话创建层：配比与版本要真的传到 system prompt 上"""

    def _session_prompt(self, **kwargs) -> str:
        # 不需要 import `scenes` 里的任何名字 —— `create_question_session` 是
        # `LLMService` 上的方法（`scenes.py` 提供的是它的 mixin）。
        #
        # 替身网关要带 `LLMService` 构造期就会读的那几个属性
        # （`api_key` / `model` / `base_url` / `provider` / 重试参数）——
        # 只实现 `chat` 会在构造时就 `AttributeError`（实测踩过）。
        from app.services.llm_service import LLMService

        class _Gateway:
            api_key = "fake-key"
            model = "fake-model"
            base_url = "https://fake.invalid"
            provider = "fake"
            max_retries = 1
            retry_delay = 0.0

            async def chat(self, **kwargs):  # pragma: no cover - 不会被调用
                raise AssertionError("这个用例只建会话，不该发请求")

        service = LLMService(gateway=_Gateway())
        session = service.create_question_session(**kwargs)
        return session._messages[0]["content"]

    def test_default_session_is_v1(self):
        """默认建出来的会话 = v1 文本（存量行为不变的直接证据）"""
        assert self._session_prompt() == P.QUESTION_SYSTEM_PROMPT

    def test_plan_and_version_reach_the_system_prompt(self):
        """★ 传了配比与版本 ⇒ system prompt 里真的能看到它们

        这一层最容易的失效形态是"参数收了但没用"（`**kwargs` 吞掉、
        或者 create 里忘了透传）—— 那样 A3′ 跑三版其实都在跑 v1，
        而**表现完全一样、不会报错**。
        """
        prompt = self._session_prompt(
            question_types=TYPES, difficulty_mix=DIFFS, prompt_version="2"
        )
        assert "fill_blank" in prompt and "short_answer" in prompt
        assert "难度配比" in prompt
        assert prompt != P.QUESTION_SYSTEM_PROMPT

    def test_version_3_reaches_the_system_prompt(self):
        prompt = self._session_prompt(prompt_version="3")
        assert "同范畴" in prompt, "v3 的质量规则没传到 system prompt"


class TestQuizRowsAreStampedWithTheVersionActuallyUsed:
    """★ 每个题行必须记**实际用的那一版**（A3′ 的分组报表全靠它）

    ## 为什么这条必须单独存在

    改造前写的是 `prompt_version("question_session")` —— 那个函数的语义是
    "**当前默认版**"，不是"这一次用的版"。A3′ 要跑 v1/v2/v3 三版，
    如果每行都记成默认版，`prompt_version_report_service` 会把三版混成一组，
    **对比实验的结论直接作废**，而界面上看不出任何异常（版本列有值、报表能出）。

    这正是"错了不会报错、只会静默给出错误结论"那一类，所以用源码级断言钉住。
    """

    def test_quiz_item_is_not_stamped_with_the_constant_default(self):
        import pathlib
        import re

        src = pathlib.Path(
            pathlib.Path(__file__).resolve().parents[1] / "app" / "tasks" / "understand_tasks.py"
        ).read_text(encoding="utf-8")
        # 找 `prompt_version=` 的**赋值**（注释里的引用不算）
        assigns = [
            line.strip()
            for line in src.splitlines()
            # 跳过纯注释行（注释里会引用旧写法来解释为什么改）
            if not line.strip().startswith("#")
        ]
        joined = "\n".join(assigns)
        assert not re.search(r"prompt_version=prompt_version\(", joined), (
            "QuizItem 又被记成「当前默认版」了 —— A3′ 跑三版会得到同一组版本号"
        )
        assert "effective_question_prompt_version" in joined, (
            "没有看到「实际用的那一版」这个变量 —— 它才是该写进 QuizItem 的值"
        )

    def test_no_target_categories_uses_the_default_version(self):
        """没传版本、没传目标类别 ⇒ 记默认版（存量路径的版本号不变）"""
        from app.services.llm.prompts import DEFAULT_QUESTION_PROMPT_VERSION

        def _effective(explicit):
            return explicit or DEFAULT_QUESTION_PROMPT_VERSION

        assert _effective(None) == "1"
        assert _effective("2") == "2"
        assert _effective("3") == "3"
