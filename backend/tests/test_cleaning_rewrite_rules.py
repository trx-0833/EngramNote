# -*- coding: utf-8 -*-
"""
清洗的改写类规则（B1）
======================

## 这一组为什么存在

用户在 `使用时的想法.txt` 第 5 条点名了三件事（公式简化 / 标题层级 / 多余空格），
而走查报告 §5 实测：**一条都没做**，且对 Markdown 输入逐字节空转（711 → 711）。

本轮实现了它们。这三条与清洗长期承诺的「仅做减法、不改变原意」**不同** ——
它们改变字符，所以每一条都必须把**边界**钉死：

1. **代码块 / 数学块内部一律不碰**（既有清洗规则就守着这条边界，新规则沿用同一套状态）；
2. **公式简化只认"简单公式"** —— 命令走白名单，遇到不认识的命令 / 上下标 /
   分组 / 希腊字母就**整条原样保留**。改错一个公式比不改严重得多；
3. **标题层级只改 `#` 的数量** —— 标题文字与行序一个字符都不动，
   且推不出层级的标题（没有编号的）一律不碰。

## 为什么每条规则都有"反向断言"

只测"该改的改了"是不够的：把规则写成"见公式就拍平"也能让那些用例绿，
而那正是这条规则最危险的失效形态。所以每条都有"**不该改的必须原样**"。
"""

from __future__ import annotations

import pytest

from app.services.cleaning_service import (
    clean_rules,
    infer_heading_level,
    normalize_heading_levels,
    normalize_spacing,
    simplify_inline_math,
)


class TestSimplifyInlineMath:
    """公式简化（`clean_simplify_formula`，默认关）"""

    def test_user_named_case_is_simplified_to_the_exact_target(self):
        """★ 用户点名的那个例子，改完必须**逐字**等于他写的目标形态

        原话：「`$6.34m \\times 61.95m$` 换成 `6.34m*61.95m` 这样更方便我编辑」。
        注意目标里 `*` **两侧没有空格** —— 第一版实现留下 `6.34m *61.95m`，
        形状对了但不是他要的那个。
        """
        got, n = simplify_inline_math(r"长度 $6.34m \times 61.95m$")
        assert got == "长度 6.34m*61.95m", f"实际得到 {got!r}"
        assert n == 1

    def test_whitelist_commands_are_converted(self):
        """白名单里的命令逐个验（`\times` / `\cdot` / `\div` / `\pm` …）"""
        cases = [
            (r"$a \times b$", "a*b"),
            (r"$a \cdot b$", "a*b"),
            (r"$a \div b$", "a/b"),
            (r"$a \leq b$", "a<=b"),
            (r"$a \geq b$", "a>=b"),
        ]
        for src, want in cases:
            got, _ = simplify_inline_math(src)
            assert got == want, f"{src!r} → {got!r}，期望 {want!r}"

    @pytest.mark.parametrize(
        "src",
        [
            r"$\frac{a}{b}$",       # 分数：拍平会丢分组
            r"$\sqrt{2}$",          # 根号
            r"$x^2 + 1$",           # 上标
            r"$a_i$",               # 下标
            r"$\alpha + \beta$",    # 希腊字母（不在白名单）
            r"$\int_0^1 f$",        # 积分
            r"$\sum_{i=1}^n$",      # 求和
            r"$\begin{matrix}a\end{matrix}$",  # 环境
        ],
    )
    def test_complex_math_is_left_untouched(self, src):
        """★ 反向断言：**不认识的**数学结构必须整条原样保留

        这条是这一组里最重要的一条 —— 把公式拍平也能让上面那些用例绿，
        而拍平 `\frac{a}{b}` 会丢掉分组、拍平 `x^2` 会变成 `x2`。
        """
        got, n = simplify_inline_math(src)
        assert got == src, f"{src!r} 被改成了 {got!r} —— 复杂公式必须原样保留"
        assert n == 0, "计数把没改的也算成改过了"

    def test_mixed_line_only_simplifies_the_simple_one(self):
        """同一行里简单与复杂公式并存时，只动简单的那条"""
        got, n = simplify_inline_math(r"混合 $3 \cdot 4$ 和 $\sqrt{2}$")
        assert got == r"混合 3*4 和 $\sqrt{2}$", got
        assert n == 1

    def test_plain_text_is_untouched(self):
        """没有公式的普通行**一个字符都不变**（`n == 0`）"""
        src = "这是一句普通的说明，含 3 个数字与 a 字母。"
        got, n = simplify_inline_math(src)
        assert got == src
        assert n == 0

    def test_left_right_are_stripped_with_their_parens(self):
        """`\\left(` / `\\right)` 退化成普通括号（白名单里映射成空串）"""
        got, _ = simplify_inline_math(r"$\left( a + b \right)$")
        assert got == "(a+b)", got


class TestNormalizeSpacing:
    """多余空格压缩（`clean_normalize_spacing`，默认开）"""

    def test_multiple_spaces_collapse_to_one(self):
        """连续 ≥2 个空格压成 1 个（用户点名的"前后各空一格"的实际形态）"""
        assert normalize_spacing("尺寸 10  台")[0] == "尺寸 10 台"
        assert normalize_spacing("价格  10 元")[0] == "价格 10 元"

    def test_single_space_is_kept(self):
        """★ 反向断言：**单个**空格是正确排版，不许删

        `220 kV` 里的那一个是标准写法；删掉会变成 `220kV` ——
        那是"改写"而不是"去掉多余的"。
        """
        assert normalize_spacing("电压 220 kV")[0] == "电压 220 kV"

    def test_indent_is_preserved(self):
        """行首缩进有语义（列表 / 代码块），必须原样保留"""
        got, n = normalize_spacing("  indented  x")
        assert got == "  indented x", got
        assert n == 1

    def test_inline_code_and_math_are_untouched(self):
        """★ 行内代码与行内公式内部的空格是**内容**，不许动"""
        assert normalize_spacing("$a  b$")[0] == "$a  b$"
        assert normalize_spacing("`x  y`")[0] == "`x  y`"

    def test_plain_line_without_extra_spaces_is_unchanged(self):
        src = "正常的一句话，没有多余空格"
        assert normalize_spacing(src) == (src, 0)


class TestInferHeadingLevel:
    """编号 → 层级（`infer_heading_level`）"""

    @pytest.mark.parametrize(
        "title,want",
        [
            ("1 概述", 2),
            ("1.2 细节", 3),
            ("1.2.3 更深", 4),
            ("第一章 总则", 2),
            ("（1）小点", 3),
        ],
    )
    def test_numbered_titles_get_a_level(self, title, want):
        assert infer_heading_level(title) == want

    @pytest.mark.parametrize("title", ["概述", "前言", "附录", "参考资料"])
    def test_unnumbered_titles_return_none(self, title):
        """★ 反向断言：没有编号的标题**推不出**层级，返回 `None` 而不是猜一个

        "`## 概述` 该是 2 级还是 3 级"取决于它在文档里的位置，单看一行推不出来。
        猜一个等于把用户的结构改错 —— 而这条规则的价值恰恰是"让层级可区分"。
        """
        assert infer_heading_level(title) is None


class TestNormalizeHeadingLevels:
    """标题层级重排（`clean_normalize_heading_levels`，默认开）"""

    def test_levels_follow_the_numbering(self):
        md = "\n".join(["# 文档", "## 1 概述", "## 1.2 细节", "### 1.2.3 更深"])
        out, n = normalize_heading_levels(md)
        assert out == "\n".join(["# 文档", "## 1 概述", "### 1.2 细节", "#### 1.2.3 更深"])
        assert n == 2

    def test_title_text_is_not_touched(self):
        """★ 只改 `#` 的数量：标题文字与行序一个字符都不动"""
        md = "## 1.2 细节（含 `code` 与 **强调**）"
        out, _ = normalize_heading_levels(md)
        assert out == "### 1.2 细节（含 `code` 与 **强调**）", out

    def test_unnumbered_headings_keep_their_level(self):
        """★ 反向断言：没有编号的标题层级原样保留"""
        md = "\n".join(["# 文档", "## 概述", "### 随便写的层级"])
        out, n = normalize_heading_levels(md)
        assert out == md, "没有编号的标题被改了层级"
        assert n == 0

    def test_hash_inside_code_fence_is_not_a_heading(self):
        """★ `# shell 注释` 在围栏里是注释，不是标题"""
        md = "\n".join(["# 文档", "```", "# 1 这不是标题", "```"])
        out, n = normalize_heading_levels(md)
        assert out == md, f"代码块里的 # 被当成标题改了：{out!r}"
        assert n == 0


class TestCleanRulesIntegration:
    """三条规则接进 `clean_rules` 之后的行为（含开关）"""

    def _text(self) -> str:
        return "\n".join([
            "# 报告",
            "## 1 概况",
            "## 1.1 尺寸",
            r"长度 $6.34m \times 61.95m$，重量 10  吨。",
            "## 1.1.1 备注",
            r"复杂公式 $\frac{a}{b}$ 保持原样。",
            "```",
            "code  with   spaces",
            "# 1 注释不是标题",
            "```",
        ])

    def test_default_switches_do_not_simplify_formula_but_do_fix_headings(self):
        """★ 默认姿态：公式简化**关**（产物逐字节不变）、标题层级**开**、空格**开**"""
        from app.config import get_settings

        s = get_settings()
        assert s.clean_simplify_formula is False, "公式简化必须默认关（它改变正文语义）"
        assert s.clean_normalize_heading_levels is True
        assert s.clean_normalize_spacing is True

        out, stats = clean_rules(self._text())

        # 公式原样（默认关）
        assert r"$6.34m \times 61.95m$" in out, out
        assert stats["formula_simplified"] == 0
        # 标题层级改了
        assert "### 1.1 尺寸" in out and "#### 1.1.1 备注" in out
        assert stats["heading_levels_fixed"] == 2
        # 空格压了（正文那一处）
        assert "重量 10 吨" in out
        assert stats["spacing_normalized"] >= 1

    def test_code_fence_content_is_byte_identical(self):
        """★ 围栏内**一个字符都不变**（空格没压、`#` 没被当标题）

        这是新规则最该守住的边界：代码块里的空格与 `#` 都是内容。
        """
        out, _ = clean_rules(self._text())
        assert "code  with   spaces" in out, "代码块里的空格被压了"
        assert "# 1 注释不是标题" in out, "代码块里的 # 被当成标题改了"

    def test_enabling_formula_switch_simplifies(self):
        """把开关打开后公式会被简化（证明开关真的接线了，不是装饰）

        ⚠️ 改的是 **`cleaning_service.settings`**（模块级那份），不是
        `get_settings()` 的返回值 —— `clean_rules` 读的是前者。
        第一版改了 `get_settings()` 得到的新实例，于是"开关打开"对被测函数
        毫无影响，用例红得很有教育意义：**测开关的用例必须打到被测函数真正读的那份配置上**。
        """
        from app.services import cleaning_service as cs

        old = cs.settings.clean_simplify_formula
        cs.settings.clean_simplify_formula = True
        try:
            out, stats = clean_rules(self._text())
        finally:
            cs.settings.clean_simplify_formula = old

        assert "6.34m*61.95m" in out, out
        assert stats["formula_simplified"] == 1
        # 复杂公式仍然原样
        assert r"$\frac{a}{b}$" in out
        # 代码块仍然原样
        assert "code  with   spaces" in out

    def test_stats_keys_are_additive(self):
        """新增的统计键是**纯增量** —— 前端「清洗统计」面板不需要改形状"""
        _, stats = clean_rules("普通文本\n")
        for key in ("formula_simplified", "spacing_normalized", "heading_levels_fixed"):
            assert key in stats, f"缺统计键 {key}"
            assert isinstance(stats[key], int)
        # 旧键一个都不能少
        for key in ("empty_lines_removed", "headers_footers_removed", "watermarks_removed"):
            assert key in stats, f"旧统计键 {key} 被弄丢了"
