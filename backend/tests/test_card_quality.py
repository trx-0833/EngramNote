# -*- coding: utf-8 -*-
"""
卡片质量打分器（A4）
=====================

## 这一组盯的是什么

打分器是**启发式**的（读不懂语义），所以它最大的风险不是"漏掉坏卡"
（那只是少筛出一些），而是**误判好卡** —— 一份把正常卡片列进"低分清单"的报告，
会让人花时间去核对，然后**不再信任这份报告**。

所以每条规则都配一条**反向断言**：拿真库里的**好卡**（内容够长、有信息量）
证明它**不会**被误判。

## 打分器**只读**（这一组也守这条）

`docs/agent-workstream-plan.md` §5 第 6 条已拍板：A4 **只产出报告与标记，
不删卡、不合并、不改复习历史**。真去重合并会动 `review_states`（复习历史
会变孤儿行），要另开一轮单独批。所以本文件最后有一组"只读性"的守卫。
"""

from __future__ import annotations


from app.services.card_quality_service import (
    SEVERE_CHARS,
    SUSPICIOUS_CHARS,
    build_report,
    render_markdown,
    score_card,
)


def _card(title="标题", content="内容", card_type="concept", **over):
    return {"id": over.pop("id", "c1"), "title": title, "content": content,
            "card_type": card_type, "note_id": over.pop("note_id", "n1"), **over}


class TestTitleEcho:
    """「只是把标题说了一遍」—— 真库里最典型的那一类"""

    def test_tautology_is_flagged(self):
        """★ 真库实例：内容与标题逐字相同（只差句号）

        `'变压器急救负载运行时需加强巡视'` → `'变压器急救负载运行时需加强巡视。'`
        """
        got = score_card(_card(
            title="变压器急救负载运行时需加强巡视",
            content="变压器急救负载运行时需加强巡视。",
        ))
        assert "title_echo" in got.reasons, f"没判成标题回响：{got.reasons}"

    def test_near_tautology_with_a_few_extra_words_is_flagged(self):
        """真库实例：标题的名词加一句最短的谓语"""
        got = score_card(_card(title="各部分温度监视", content="监视各部分温度正常。"))
        assert "title_echo" in got.reasons

    def test_long_content_mentioning_the_title_is_not_flagged(self):
        """★★ 反向断言：**长**内容里反复出现标题词是**正常**的

        "变压器"在一段讲变压器的正文里当然反复出现 ——
        若判据只看"回响比例"而不加长度上限，几乎所有好卡都会被误判。
        """
        got = score_card(_card(
            title="变压器瓦斯保护",
            content=(
                "变压器瓦斯保护是变压器内部故障的主保护。当变压器内部发生"
                "匝间短路或相间短路时，变压器油分解产生气体，气体继电器动作，"
                "变压器瓦斯保护瞬时动作于信号或跳闸。轻瓦斯动作于信号，"
                "重瓦斯动作于跳闸，二者判据是气体量与油流速度。"
            ),
        ))
        assert "title_echo" not in got.reasons, "长内容被误判成标题回响"

    def test_short_but_substantive_content_is_not_title_echo(self):
        """★★ 反向断言：内容**补充了标题没有的字**时不许判标题回响

        真库实例：`'泄洪放空洞洞型'` → `'泄洪放空洞为有压洞。'`
        内容是短，但"有压洞"三个字是标题里没有的**信息**。
        实测它的回响比例是 **0.83** —— 而 0.80-0.83 这一档里既有该判的
        也有不该判的，所以判据只能取 `== 1.0`。这条用例锁的就是那个取舍：
        **一旦有人把阈值放宽到 0.8 去"多抓一点"，它立刻红。**
        """
        got = score_card(_card(title="泄洪放空洞洞型", content="泄洪放空洞为有压洞。"))
        assert "title_echo" not in got.reasons, (
            f"「有压洞」是标题里没有的信息，不该判成回响（阈值被放宽了？）：{got.reasons}"
        )

    def test_another_substantive_short_card(self):
        """同类反例：`'泄洪放空洞检修门配置'` → `'泄洪放空洞设1孔检修门。'`（比例 0.80）

        "1孔"是实打实的信息 —— 这张卡虽然短，但它**说了事**。
        """
        got = score_card(_card(
            title="泄洪放空洞检修门配置", content="泄洪放空洞设1孔检修门。"
        ))
        assert "title_echo" not in got.reasons, got.reasons

    def test_echo_threshold_is_strict_one(self):
        """阈值常量本身锁在 1.0（放宽它会让上面两条反向断言失去意义）"""
        from app.services.card_quality_service import TITLE_ECHO_RATIO

        assert TITLE_ECHO_RATIO == 1.0, (
            "标题回响阈值被放宽了 —— 0.80-0.83 那一档没有可用分界，"
            "放宽只会引入误报（见 score_card 里的实测表）"
        )

    def test_empty_content_is_flagged(self):
        """内容为空 ⇒ 回响比例 1.0（它"完全没补充"）"""
        got = score_card(_card(title="冷备用状态", content=""))
        assert "title_echo" in got.reasons
        assert "too_short_severe" in got.reasons


class TestLength:
    def test_severe_threshold(self):
        got = score_card(_card(content="字" * SEVERE_CHARS))
        assert "too_short_severe" in got.reasons

    def test_suspicious_threshold(self):
        got = score_card(_card(content="字" * (SEVERE_CHARS + 1)))
        assert "too_short" in got.reasons
        assert "too_short_severe" not in got.reasons

    def test_above_threshold_is_not_flagged_for_length(self):
        got = score_card(_card(
            title="浮充",
            content="浮充是蓄电池的一种运行方式，" * 3,
        ))
        assert "too_short" not in got.reasons
        assert "too_short_severe" not in got.reasons

    def test_thresholds_are_ordered(self):
        """两个阈值必须有意义地错开（相等会让"轻/重"两档混成一档）"""
        assert SEVERE_CHARS < SUSPICIOUS_CHARS


class TestMetaOnly:
    def test_self_referential_placeholder_is_flagged(self):
        """「见下表」这类自指没有实质内容"""
        for content in ("见下表。", "参见附录 A。", "如下所示。", "略。", "待补充"):
            got = score_card(_card(title="设备参数", content=content))
            assert "meta_only" in got.reasons, f"{content!r} 没被识别为自指"

    def test_normal_sentence_starting_with_see_is_not_flagged(self):
        """★ 反向断言：正常句子以"见"开头不该被误判

        `'见证了……'`、`'见诸……'` 都是正常表达。判据锚在**指向语 + 指代物**
        （下表 / 附录 / 图）或**单个占位词**上，不是"以见字开头"。
        """
        got = score_card(_card(
            title="水电站投产",
            content="见证了我国水电从跟跑到领跑的全过程，装机容量居世界首位。",
        ))
        assert "meta_only" not in got.reasons, f"误判：{got.reasons}"

    def test_short_normal_sentence_starting_with_see_is_also_safe(self):
        """★ 更难的版本：**短**句子以"见"开头也不该误判

        第一版判据（`^(见|参见|…)`）在长句上侥幸没红，因为长句被别的规则
        接住了；这条把内容压到 30 字以内，专门逼出那个误判。
        """
        got = score_card(_card(title="见证", content="见证了水电从跟跑到领跑。"))
        assert "meta_only" not in got.reasons, f"短正常句被误判成自指：{got.reasons}"


class TestTypeMismatch:
    def test_formula_card_without_math_symbols_is_flagged(self):
        """真库实例：`card_type='formula'` 但内容是一句叙述"""
        got = score_card(_card(
            title="十一五关停小火电效益",
            content="关停7682.5万kW，年节约燃煤8700万t。",
            card_type="formula",
        ))
        # 内容里有数字 ⇒ 不算"没有数学符号"
        assert "type_mismatch" not in got.reasons

    def test_formula_card_with_pure_prose_is_flagged(self):
        got = score_card(_card(title="计算公式", content="按规程计算即可。", card_type="formula"))
        assert "type_mismatch" in got.reasons

    def test_non_formula_cards_are_never_flagged_for_this(self):
        """★ 反向断言：这条规则**只**管 formula 卡，不能波及 concept/definition/qa"""
        for ctype in ("concept", "definition", "qa"):
            got = score_card(_card(title="甲", content="按规程计算即可。", card_type=ctype))
            assert "type_mismatch" not in got.reasons, f"{ctype} 被类型规则误判"


class TestScoreIsOnlyForOrdering:
    def test_clean_card_has_full_score_and_no_reasons(self):
        got = score_card(_card(
            title="瓦斯保护",
            content=(
                "瓦斯保护是变压器内部故障的主保护，轻瓦斯动作于信号，"
                "重瓦斯动作于跳闸，判据是气体量与油流速度。"
            ),
        ))
        assert got.reasons == []
        assert got.score == 100

    def test_score_never_goes_negative(self):
        got = score_card(_card(title="", content=""))
        assert got.score >= 0

    def test_more_hits_sort_earlier(self):
        """命中越多分数越低（分数只用于排序，这是它的唯一职责）"""
        bad = score_card(_card(title="", content=""))
        mild = score_card(_card(content="字" * (SEVERE_CHARS + 1)))
        assert bad.score < mild.score

    def test_evidence_is_present_and_specific(self):
        """★ 每条判定都要有**人能据此否掉误判**的证据

        报告里给一个"62 分"是没法反驳的；给"内容 12 字符、标题 15 字符"
        就能让人一眼看出"哦它确实太短"或者"这条我不认同"。
        """
        got = score_card(_card(title="甲" * 15, content="字" * 12))
        assert got.evidence
        assert "12" in got.evidence and "15" in got.evidence

    def test_excerpt_is_truncated(self):
        got = score_card(_card(content="字" * 200))
        assert len(got.excerpt) <= 81
        assert got.excerpt.endswith("…")


class TestBuildReport:
    def test_counts_and_grouping(self):
        rows = [
            _card(id="c1", title="甲", content="字" * 10),
            _card(id="c2", title="乙", content="字" * 25),
            _card(id="c3", title="丙", content="内容够长的一张正常卡片，" * 5),
        ]
        report = build_report(rows)
        assert report["total"] == 3
        assert report["flagged"] == 2
        assert report["by_reason"]["too_short_severe"] == 1
        assert report["by_reason"]["too_short"] == 1

    def test_only_suspicious_filters_clean_cards(self):
        rows = [_card(id="c1", content="字" * 10), _card(id="c2", content="正常内容" * 20)]
        assert len(build_report(rows)["cards"]) == 1
        assert len(build_report(rows, only_suspicious=False)["cards"]) == 2

    def test_order_is_deterministic_and_worst_first(self):
        """★ 同一份输入每次得到**同样的顺序**，且最可疑的在最前

        报告要能对着两次运行做 diff，顺序就必须确定；
        并列时用 `card_id` 兜底（否则集合/字典顺序一变，报告就"变了"）。
        """
        rows = [
            _card(id=f"c{i}", title=f"标题{i}", content="字" * (10 + i))
            for i in range(6)
        ]
        a = [c.card_id for c in build_report(rows)["cards"]]
        b = [c.card_id for c in build_report(list(reversed(rows)))["cards"]]
        assert a == b, "同一批卡片换个输入顺序，报告顺序就变了"
        assert a[0] == "c0", "最短的那张没排在最前"

    def test_empty_input(self):
        report = build_report([])
        assert report == {"total": 0, "flagged": 0, "by_reason": {}, "cards": []}


class TestRenderMarkdown:
    def test_disclaimer_is_always_present(self):
        """★★ 报告开头**必须**写清"这是启发式筛查、没有删任何东西"

        没有这句话，一份名叫《低分卡清单》的文档被当成"待删清单"是迟早的事
        —— 而 `AGENTS.md` §6 记过「清理副本却删掉真身」那类事故。
        """
        md = render_markdown(build_report([]))
        assert "没有删除" in md
        assert "读不懂语义" in md
        assert "否掉误判" in md

    def test_merge_and_delete_is_declared_out_of_scope(self):
        md = render_markdown(build_report([]))
        assert "另开一轮单独批" in md, "没写清「合并/删除不在这份报告范围内」"
        assert "review_states" in md, "没提合并会动复习历史"

    def test_no_dangerous_verbs(self):
        """报告里不该出现任何"删除/合并"的祈使句（防被当成操作手册）"""
        md = render_markdown(build_report([_card(content="字" * 5)]))
        for verb in ("执行删除", "请删除", "立即合并", "批量删除"):
            assert verb not in md, f"报告里出现了操作指令：{verb}"

    def test_empty_report_says_so(self):
        md = render_markdown(build_report([]))
        assert "没有命中任何规则" in md

    def test_table_escapes_pipes(self):
        """内容里的 `|` 会破坏 markdown 表格 —— 必须转义"""
        md = render_markdown(build_report([_card(title="甲|乙", content="a|b" * 5)]))
        assert "\\|" in md

    def test_duplicate_section_only_when_given(self):
        base = render_markdown(build_report([]))
        assert "重复标题清单" not in base
        with_dup = render_markdown(build_report([]), duplicate_section={
            "title_groups": 72, "cards_involved": 187,
        })
        assert "重复标题清单" in with_dup
        assert "72" in with_dup and "187" in with_dup


class TestReadOnly:
    """★ A4 **不许**有任何写操作（已拍板：只标记不删）"""

    def test_service_module_has_no_write_calls(self):
        import pathlib
        import re

        src = pathlib.Path(
            pathlib.Path(__file__).resolve().parents[1]
            / "app" / "services" / "card_quality_service.py"
        ).read_text(encoding="utf-8")
        # 去掉注释与 docstring 里的说明性文字，只看**代码**
        code_lines = [
            ln for ln in src.splitlines()
            if not ln.strip().startswith("#")
        ]
        code = "\n".join(code_lines)
        for forbidden in ("session.add(", "db.delete(", ".commit()", "execute("):
            assert forbidden not in code, (
                f"card_quality_service 里出现了写操作/DB 访问：{forbidden} —— "
                f"A4 只打分，不落库不改数据"
            )
        # 反过来：它必须真的**没有** import 数据库相关的东西
        assert not re.search(r"^from .*database import", code, re.MULTILINE)

    def test_report_script_opens_the_db_read_only(self):
        """脚本必须以 `mode=ro` 打开库（比"我保证不写"可靠）"""
        import pathlib

        src = pathlib.Path(
            pathlib.Path(__file__).resolve().parents[1]
            / "scripts" / "report_card_quality.py"
        ).read_text(encoding="utf-8")
        assert "mode=ro" in src, "报告脚本没有以只读模式打开数据库"
