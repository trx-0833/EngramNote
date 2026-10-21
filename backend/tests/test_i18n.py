"""i18n 基础设施的测试（阶段 0）

## 这个文件守的是什么

三块东西，都是"错了不会报错、只会静默表现成别的问题"的那类：

| 被测对象 | 错了会怎样 |
|---|---|
| `parse_accept_language` | 英文客户端拿到中文错误（或反之）—— 不报错，只是语言不对 |
| 错误文案查表 | 表里的值与 `raise` 时写的中文不一致 → **悄悄改了现有用户看到的中文提示** |
| `context` 里的 locale | 请求之间串号 —— 只在并发下偶发，极难查 |

## 为什么"表与调用点一致"要用 AST 扫

`error_messages.py` 的表项是手抄的。手抄就会漂移：把
`"邮箱或密码错误"` 抄成 `"邮箱或密码不正确"`，中文用户看到的提示就变了，
而**没有任何测试会红**（两句话都"对"）。所以这里扫全 `app/` 的
`AppError(...)` 调用点，把那个字符串取出来与表里的中文逐字比对。
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from app.core import context, i18n
from app.core.error_messages import (
    ERROR_MESSAGES,
    HTTP_STATUS_MESSAGES,
    UNTRANSLATED_CODES,
    internal_error_message,
    registered_codes,
    resolve_http_status_message,
    resolve_message,
)

APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"

#: 表里的中文**是新写的**、调用点原本是英文的错误码
#:
#: `test_fstring_templates_match_the_raise_site` 的判据是"表里的中文骨架
#: 与调用点逐字相同"。这个前提对下面这些码**不成立** —— 它们的调用点给的
#: 就是英文，所以不可能相同。放进名单是**显式豁免**，而不是把守卫关掉：
#: 以后新增的码仍然会被检查。
#:
#: - `GOAL_ACTIVE_LIMIT_REACHED`：`goal_service.py:150` 是
#:   `f"Maximum {MAX_ACTIVE_GOALS} active goals"`（实测确认，不是笔误）。
#:   本轮给它补了中文；表里的 `en` 与调用点逐字相同。
CHINESE_WRITTEN_FROM_SCRATCH: frozenset = frozenset({"GOAL_ACTIVE_LIMIT_REACHED"})


# ---------------------------------------------------------------------------
# 1. Accept-Language 解析
# ---------------------------------------------------------------------------


class TestParseAcceptLanguage:
    """`Accept-Language` → 受支持的语言"""

    def test_missing_header_falls_back_to_default(self):
        """缺头 = 保持现状（中文）—— 不能因为加了 i18n 就让现有客户端收到英文"""
        assert i18n.parse_accept_language(None) == "zh-CN"
        assert i18n.parse_accept_language("") == "zh-CN"
        assert i18n.parse_accept_language("   ") == "zh-CN"

    def test_english_variants(self):
        assert i18n.parse_accept_language("en") == "en"
        assert i18n.parse_accept_language("en-US") == "en"
        assert i18n.parse_accept_language("en-GB,en;q=0.9") == "en"

    def test_chinese_variants_including_traditional(self):
        """★ 繁体也归简体 —— 项目没有繁体文案，简体比英文更接近用户

        这条是**产品判断**，不是技术判断。前端 `normalizeLocale` 用同一条规则。
        """
        for tag in ("zh", "zh-CN", "zh-Hans", "zh-Hant", "zh-TW", "zh-HK"):
            assert i18n.parse_accept_language(tag) == "zh-CN", tag

    def test_quality_ordering_wins_over_list_order(self):
        """★ 按 `q` 权重排序，不是取第一个

        `en;q=0.5,zh-CN;q=0.9` 的语义是"中文优先"，而 `en` 排在最前 ——
        取第一个会得到用户**第二想要**的语言。
        """
        assert i18n.parse_accept_language("en;q=0.5,zh-CN;q=0.9") == "zh-CN"
        assert i18n.parse_accept_language("zh-CN;q=0.2,en;q=0.9") == "en"

    def test_unsupported_languages_fall_back(self):
        """都不支持 → 默认语言（不是空串、不是第一个语言）"""
        assert i18n.parse_accept_language("ja,ko;q=0.9") == "zh-CN"
        assert i18n.parse_accept_language("de-DE,fr;q=0.8") == "zh-CN"

    def test_supported_language_beats_unsupported_even_with_lower_q(self):
        """不支持的条目被跳过，而不是"占据最高权重后回退" """
        assert i18n.parse_accept_language("ja;q=1.0,en;q=0.1") == "en"

    def test_q_zero_means_explicitly_refused(self):
        """★ `q=0` = 明确拒绝（RFC 7231），不能当成"权重最低但仍可接受"

        `en;q=0` 是"给我别的，别给英文"。当成可接受会让明确拒绝英文的客户端
        拿到英文 —— 与它的意图正好相反。
        """
        assert i18n.parse_accept_language("en;q=0") == "zh-CN"
        assert i18n.parse_accept_language("zh-CN;q=0,en;q=0.5") == "en"

    def test_malformed_quality_treated_as_one(self):
        """畸形 `q`（`q=abc`）按 RFC 的宽松姿态当 1.0 —— 意图很清楚，不该丢弃"""
        assert i18n.parse_accept_language("en;q=abc") == "en"
        assert i18n.parse_accept_language("en;q=") == "en"

    def test_whitespace_and_case_variants(self):
        assert i18n.parse_accept_language("  EN-US  ") == "en"
        assert i18n.parse_accept_language("en-US; q=0.9") == "en"
        assert i18n.parse_accept_language("ZH-cn") == "zh-CN"

    def test_wildcard_falls_back(self):
        """`*` 不是受支持的语言标记 → 默认语言（不抛错）"""
        assert i18n.parse_accept_language("*") == "zh-CN"
        assert i18n.parse_accept_language("*,en;q=0.5") == "en"

    def test_garbage_header_does_not_raise(self):
        """畸形头不抛错 —— 这是请求头，攻击者可以直接构造"""
        for bad in (";;;", ",,,", "q=", "=;=", "en;;;q=0.5"):
            assert i18n.parse_accept_language(bad) in i18n.SUPPORTED_LOCALES

    def test_equal_quality_keeps_list_order(self):
        """权重相同时先出现的优先（稳定排序）"""
        assert i18n.parse_accept_language("en;q=0.9,zh-CN;q=0.9") == "en"
        assert i18n.parse_accept_language("zh-CN;q=0.9,en;q=0.9") == "zh-CN"


class TestNormalizeLocale:
    """`normalize_locale` 与前端 `normalizeLocale` 是同一条规则的两份实现"""

    @pytest.mark.parametrize(
        "tag,expected",
        [
            ("zh", "zh-CN"),
            ("zh-CN", "zh-CN"),
            ("zh_Hant", "zh-CN"),
            ("zh-Hans-CN", "zh-CN"),
            ("en", "en"),
            ("en-US", "en"),
            ("EN_us", "en"),
            ("ja", None),
            ("", None),
            (None, None),
        ],
    )
    def test_normalization_table(self, tag, expected):
        assert i18n.normalize_locale(tag) == expected


# ---------------------------------------------------------------------------
# 2. 上下文里的 locale
# ---------------------------------------------------------------------------


class TestLocaleContext:
    """locale 存在 `core/context` 的 ContextVar 里 —— 必须不跨请求残留"""

    def test_default_is_empty_string_without_request(self):
        """没有请求上下文（Celery worker / 脚本）时是空串，不是 None"""
        context.reset_context()
        assert context.get_locale() == ""

    def test_get_locale_falls_back_to_default(self):
        """空串 → 默认语言（保证非空 —— 透传空串会让下游查表全部 miss）"""
        context.reset_context()
        assert i18n.get_locale() == i18n.DEFAULT_LOCALE

    def test_get_locale_reads_request_context(self):
        context.set_request_context("rid-1", locale="en")
        try:
            assert i18n.get_locale() == "en"
        finally:
            context.reset_context()

    def test_unsupported_stored_value_falls_back(self):
        """★ 存了不受支持的值也不能透传出去（透传会让查表全 miss）"""
        context.set_request_context("rid-1", locale="klingon")
        try:
            assert i18n.get_locale() == i18n.DEFAULT_LOCALE
        finally:
            context.reset_context()

    def test_reset_clears_locale(self):
        """★ `reset_context()` 必须清掉 locale

        漏掉它 = 上下文残留到下一个请求（在 `reset_context` 的显式变量元组里
        加漏一个就会这样）。那种 bug 只在并发下偶发，所以这条要钉住。
        """
        context.set_request_context("rid-1", locale="en")
        context.reset_context()
        assert context.get_locale() == ""


# ---------------------------------------------------------------------------
# 3. 错误文案查表
# ---------------------------------------------------------------------------


class TestResolveMessage:
    """`resolve_message` 的三条分支"""

    def test_registered_code_returns_translation(self):
        assert resolve_message("AUTH_INVALID_CREDENTIALS", "x", "en") == "Incorrect email or password"
        assert resolve_message("AUTH_INVALID_CREDENTIALS", "x", "zh-CN") == "邮箱或密码错误"

    def test_unregistered_code_returns_fallback(self):
        """★ 没登记的 code 用 `raise` 时那句 —— 这是"分批迁移"的关键

        没有这条回退，加 i18n 就必须一次性改完 90 处，否则未迁移的会拿到
        空串或 KeyError。
        """
        assert resolve_message("SOME_UNMIGRATED_CODE", "原有中文提示", "en") == "原有中文提示"

    def test_unknown_locale_falls_back_to_default_language(self):
        """未知语言 → 默认语言的值，而不是空串"""
        assert resolve_message("AUTH_INVALID_CREDENTIALS", "x", "klingon") == "邮箱或密码错误"

    def test_never_returns_empty(self):
        """★ 任何输入都不返回空串 —— 空串会让用户看到一个没有内容的错误框"""
        for code in ["", "NOPE", *registered_codes()]:
            for locale in ["", "en", "zh-CN", "klingon"]:
                assert resolve_message(code, "fallback", locale) != ""


class TestParamsFilling:
    """带运行期值的文案：`params` 填充（`AppError.params` → `resolve_message`）"""

    def test_params_are_filled_in_both_locales(self):
        """★ 两种语言都要填对 —— 只填一种等于另一种仍是模板"""
        assert (
            resolve_message(
                "MATERIAL_NOT_FOUND", "x", "zh-CN", {"material_id": "abc123"}
            )
            == "资料 abc123 不存在"
        )
        assert (
            resolve_message("MATERIAL_NOT_FOUND", "x", "en", {"material_id": "abc123"})
            == "Material abc123 does not exist"
        )

    def test_placeholder_names_differ_from_call_site_expressions(self):
        """★ 表里的占位符名与调用点表达式不同名也能填

        调用点写的是 `{note.status.value}`（不是合法的 format 字段名），
        表里用 `{status}` —— 两者**不必同名**，只有 `params` 的键要对上。
        """
        got = resolve_message(
            "NOTE_STATUS_INVALID", "x", "en", {"status": "cleaning"}
        )
        assert "cleaning" in got
        assert "{status}" not in got

    def test_missing_param_keeps_the_placeholder(self):
        """★ 缺参数时保留占位符，**不抛异常**

        这是错误处理路径 —— 为了报错反而自己炸掉、最后变成 500，是最糟的形态。
        保留 `{value}` 虽难看，但只是局部问题，日志里也能看出哪个参数没传。
        """
        got = resolve_message("MATERIAL_NOT_FOUND", "x", "en", {"wrong_key": "v"})
        assert got == "Material {material_id} does not exist"

    def test_no_params_returns_template_verbatim(self):
        """不传 params 时**不解析**模板（避免把文案里本就有的花括号当占位符）"""
        got = resolve_message("MATERIAL_NOT_FOUND", "x", "en")
        assert got == "Material {material_id} does not exist"

    def test_unregistered_code_with_params_returns_fallback(self):
        """未登记的码即使给了 params 也回退 `fallback`（不试图填充它）"""
        got = resolve_message("NOPE", "原中文 {v}", "en", {"v": "x"})
        assert got == "原中文 {v}"

    def test_malformed_template_does_not_raise(self):
        """花括号不配对时原样返回，不抛 —— 同上，错误路径不该二次失败"""
        from app.core.error_messages import fill_params

        assert fill_params("值 {v 无效", {"v": "x"}) == "值 {v 无效"
        assert fill_params("值 } 无效", {"v": "x"}) == "值 } 无效"

    def test_every_placeholdered_entry_fills_without_leftover_braces(self):
        """★ 表里**每一条**带占位符的文案，用其占位符名填充后都不该残留 `{`

        这条是"漏配占位符"的守卫：某条文案里写了 `{foo}` 而没人会传 `foo`
        的话，用户就会看到字面量 `{foo}`。
        """
        import string

        leftovers: list[str] = []
        for code, entry in ERROR_MESSAGES.items():
            for locale, text in entry.items():
                fields = {
                    name
                    for _, name, _, _ in string.Formatter().parse(text)
                    if name
                }
                if not fields:
                    continue
                filled = resolve_message(code, "x", locale, {f: "V" for f in fields})
                if "{" in filled or "}" in filled:
                    leftovers.append(f"{code}/{locale}: {filled!r}")
        assert not leftovers, "这些文案填充后仍残留花括号：\n  " + "\n  ".join(leftovers)


class TestHttpStatusMessages:
    """`HTTPException` 的翻译按**中文原文**查，不按状态码"""

    def test_known_detail_is_translated(self):
        assert resolve_http_status_message("无效或过期的令牌", "en") == "Invalid or expired token"

    def test_unknown_detail_passes_through(self):
        assert resolve_http_status_message("某个没登记的说法", "en") == "某个没登记的说法"

    def test_same_status_different_sentences_do_not_collide(self):
        """★ 键是原文而不是状态码 —— 同一个 401 下不同句子各有各的翻译

        若按状态码查表，这两句会共用一条文案，其中一句必然被说错。
        """
        a = resolve_http_status_message("无效或过期的令牌", "en")
        b = resolve_http_status_message("用户不存在或已禁用", "en")
        assert a != b
        assert "token" in a.lower()
        assert "user" in b.lower()


class TestHardcodedMessages:
    """中间件里硬编码的两句也要能翻译"""

    def test_internal_error(self):
        assert internal_error_message("en") == "Internal server error. Please try again later."
        assert "服务器内部错误" in internal_error_message("zh-CN")

    def test_unknown_locale_falls_back(self):
        assert internal_error_message("klingon") == internal_error_message("zh-CN")


# ---------------------------------------------------------------------------
# 4. ★ 表里的中文必须与代码里 `raise` 的那句逐字相同
# ---------------------------------------------------------------------------


def _collect_app_error_messages() -> dict[str, set[str]]:
    """扫全 `app/`，收集 `{error_code: {出现过的**中文字面量** message}}`

    只看**字面量**形式的 `AppError(CODE, "中文")` —— 变量、f-string、
    `str(e)` 这类跳过（它们本来就不能逐字进表，见 `error_messages.py` 里
    `UNTRANSLATED_CODES` 的说明）。
    """
    found: dict[str, set[str]] = {}
    for path in APP_DIR.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - 语法错误由 ruff 先发现
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name != "AppError" or len(node.args) < 2:
                continue
            code_node, msg_node = node.args[0], node.args[1]
            if not isinstance(code_node, ast.Name) or not isinstance(msg_node, ast.Constant):
                continue
            if not isinstance(msg_node.value, str):
                continue
            found.setdefault(code_node.id, set()).add(msg_node.value)
    return found


def _placeholder_skeleton(text: str) -> str:
    """把 `{任意内容}` 替换成 `{}`，得到"字面骨架"

    用于比对"表里的模板"与"调用点的 f-string" —— 两者占位符名可以不同，
    但**除了占位符以外的字**必须逐字相同。

    ⚠️ 用**非贪婪**匹配 `\\{[^{}]*\\}`：`{a} 与 {b}` 要变成两个 `{}`，
    贪婪匹配会把它整段吞成一个。
    """
    return re.sub(r"\{[^{}]*\}", "{}", text)


def _collect_app_error_fstring_templates() -> dict[str, set[str]]:
    """扫全 `app/`，收集 `{error_code: {f-string 的"骨架"}}`

    与 `_collect_app_error_messages` 互补：那个只看字面量，这个只看 f-string
    （`AppError(CODE, f"...{expr}...")`）。

    f-string 的还原方式：把 `FormattedValue` 的部分换成 `{}`，
    把 `Constant` 的文本原样拼接 —— 于是
    `f"文件大小超过限制 ({max_size // (1024*1024)}MB)"` 得到
    `"文件大小超过限制 ({}MB)"`，正好能与表里
    `"文件大小超过限制 ({max_mb}MB)"` 的骨架比对。
    """
    found: dict[str, set[str]] = {}
    for path in APP_DIR.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name != "AppError" or len(node.args) < 2:
                continue
            code_node, msg_node = node.args[0], node.args[1]
            if not isinstance(code_node, ast.Name) or not isinstance(msg_node, ast.JoinedStr):
                continue
            parts: list[str] = []
            for piece in msg_node.values:
                if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                    parts.append(piece.value)
                else:
                    parts.append("{}")  # FormattedValue
            found.setdefault(code_node.id, set()).add("".join(parts))
    return found


def _collect_app_error_codes() -> set[str]:
    """扫全 `app/`，收集**所有** `AppError(...)` 用到的 code（不论 message 形态）

    与 `_collect_app_error_messages` 的区别：这个连"message 是变量"的调用点
    也算 —— 判断"表里这个码还有没有人用"必须用这一份，否则
    `AppError(NOTE_EXPORT_EMPTY, empty_message, 400)` 这种会被误判成
    "表里登记了但代码里没人用"。
    """
    found: set[str] = set()
    for path in APP_DIR.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name != "AppError" or not node.args:
                continue
            # 也接受 `AppError(code=...)` 关键字形态
            for candidate in [node.args[0], *(kw.value for kw in node.keywords if kw.arg == "code")]:
                if isinstance(candidate, ast.Name):
                    found.add(candidate.id)
                    break
    return found


class TestTableMatchesRaiseSites:
    """★ 本文件最重要的一组断言

    `error_messages.py` 的中文值是**手抄**自调用点的。抄错一个字，
    中文用户看到的提示就悄悄变了，而**没有任何测试会红**（两句话都"对"）。
    所以这里机械比对。
    """

    def test_every_registered_code_exists_in_code(self):
        """表里的 code 必须在 `app/` 里真的被用到（防手滑写错常量名）

        ⚠️ 用 `_collect_app_error_codes`（**所有**调用点）而不是
        `_collect_app_error_messages`（只收字面量）：`NOTE_EXPORT_EMPTY`
        这类码的 message 是变量，只收字面量会把它们误判成"没人用"。
        """
        used = _collect_app_error_codes()
        unknown = [code for code in registered_codes() if code not in used]
        assert not unknown, (
            f"这些 code 在 error_messages.py 里登记了，但 app/ 里没有任何 "
            f"AppError(...) 调用点使用它们：{unknown}\n"
            "（可能是常量名抄错，或那个调用点已被删掉而表没跟着清）"
        )

    def test_untranslated_codes_really_are_absent_from_the_table(self):
        """★ `UNTRANSLATED_CODES` 里的码**确实**不在表里

        这份名单的价值在于让"漏了"与"刻意没做"能被区分开。若某个码
        既在名单里、又进了表，那名单就在说谎 —— 后人照着它做决策会出错。
        """
        overlap = sorted(set(registered_codes()) & UNTRANSLATED_CODES)
        assert not overlap, (
            f"这些 code 既在 UNTRANSLATED_CODES 里、又进了 ERROR_MESSAGES：{overlap}\n"
            "（要么从名单里删掉它，要么把表项删掉 —— 两者只能有一个）"
        )

    def test_untranslated_codes_are_real_codes(self):
        """名单里的码必须是 `app/` 里真实存在的 code（防名单腐化成垃圾）"""
        used = _collect_app_error_codes()
        unknown = sorted(UNTRANSLATED_CODES - used)
        assert not unknown, f"UNTRANSLATED_CODES 里有 app/ 中不存在的 code：{unknown}"

    def test_every_code_is_either_translated_or_known_untranslated(self):
        """★ 每个用到的 code 要么进了表、要么在"刻意未翻译"名单里

        这条是本阶段真正的**覆盖率断言**：新增一个 `AppError` 而两者都没登记时，
        它会红。没有它的话，"漏了一个码"只能靠人去比对两份清单。
        """
        used = _collect_app_error_codes()
        covered = set(registered_codes()) | UNTRANSLATED_CODES
        missing = sorted(used - covered)
        assert not missing, (
            f"这些 code 既没进 ERROR_MESSAGES、也不在 UNTRANSLATED_CODES 里：{missing}\n"
            "请二选一：翻译它（加表项），或明确登记为刻意未翻译（加名单 + 写理由）"
        )

    def test_chinese_value_matches_the_raise_site_verbatim(self):
        """★ 中文值必须与 `raise` 时写的那句**逐字相同**

        不一致 = 迁移这个 code 等于悄悄改了现有用户看到的中文提示。
        正确做法是从调用点把字符串复制进表，而不是重写一遍。
        """
        sites = _collect_app_error_messages()
        mismatches: list[str] = []
        for code, entry in ERROR_MESSAGES.items():
            expected = entry.get("zh-CN")
            actual = sites.get(code)
            if not actual:
                continue
            if expected not in actual:
                mismatches.append(
                    f"{code}:\n    表里写的是 {expected!r}\n    代码里是 {sorted(actual)!r}"
                )
        assert not mismatches, (
            "error_messages.py 的中文值与调用点不一致：\n  " + "\n  ".join(mismatches)
        )

    def test_fstring_templates_match_the_raise_site(self):
        """★ f-string 调用点的**骨架**必须与表里的模板一致（占位符名除外）

        ## 为什么需要这一条

        `test_chinese_value_matches_the_raise_site_verbatim` 只看**字面量**
        （`_collect_app_error_messages` 跳过 f-string）。而带 `params` 的文案
        在调用点**就是 f-string** —— 于是那 15 个码没有任何守卫：
        表里写"文件大小超过限制 ({max_mb}MB)"、代码里改成"文件过大（{max_mb}MB）"，
        两边都合法、测试全绿，而**表里的中文永远匹配不上**，
        英文界面下静默回退成中文。

        ## 判据

        把 f-string 的 `{表达式}` 全部替换成 `{}` 占位，表里的 `{名字}` 也
        替换成 `{}`，然后比对**字面骨架**。占位符名故意不比对 ——
        表里用 `{status}`、调用点写 `{note.status.value}` 是**允许**的
        （见 `error_messages.py` 文件头第 1 条约定）。
        """
        sites = _collect_app_error_fstring_templates()
        mismatches: list[str] = []
        for code, templates in sites.items():
            if code in CHINESE_WRITTEN_FROM_SCRATCH:
                # 调用点原本就是英文（见名单的说明）—— 表里的中文是新写的，
                # 不可能与调用点逐字相同，所以这条守卫对它不适用
                continue
            # ⚠️ 要查**该码的全部条目**（主项 + 所有 `_ALT`），不能只看主项：
            # 同码多文案是常态（`UPLOAD_FORMAT_UNSUPPORTED` 有 4 种），
            # f-string 那个变体登记在 `_ALT2` 里，只看主项会误报。
            skeletons = {
                _placeholder_skeleton(entry.get("zh-CN") or "")
                for key, entry in ERROR_MESSAGES.items()
                if key == code or key.startswith(f"{code}_ALT")
            }
            if not skeletons or skeletons == {""}:
                continue  # 未进表的码由另一条守卫负责
            if not any(_placeholder_skeleton(t) in skeletons for t in templates):
                mismatches.append(
                    f"{code}:\n    表里骨架 {sorted(skeletons)!r}\n"
                    f"    代码里骨架 {sorted(templates)!r}"
                )
        assert not mismatches, (
            "表里的模板骨架与 f-string 调用点不一致（占位符名可以不同，其余必须逐字相同）：\n  "
            + "\n  ".join(mismatches)
        )

    def test_every_entry_has_all_supported_locales(self):
        """★ 每种语言都要有 —— 缺一种会回退到中文（英文用户看到中文提示）"""
        for code, entry in ERROR_MESSAGES.items():
            missing = [loc for loc in i18n.SUPPORTED_LOCALES if not entry.get(loc)]
            assert not missing, f"{code} 缺少这些语言的文案：{missing}"

    def test_no_empty_translations(self):
        for code, entry in ERROR_MESSAGES.items():
            for locale, text in entry.items():
                assert text.strip(), f"{code} / {locale} 的文案是空的"

    def test_http_status_table_has_all_locales(self):
        for detail, entry in HTTP_STATUS_MESSAGES.items():
            missing = [loc for loc in i18n.SUPPORTED_LOCALES if not entry.get(loc)]
            assert not missing, f"{detail!r} 缺少这些语言的文案：{missing}"

    def test_locale_setting_pattern_matches_supported_locales(self):
        """★ `LocaleSettingRequest` 的 pattern 必须与 `SUPPORTED_LOCALES` 一致

        不一致的表现是"前端能切到某种语言，但保存时报 422"——
        而界面本身已经切过去了，所以那个 422 只在刷新后才暴露。
        """
        import re

        from app.schemas.user import LocaleSettingRequest

        pattern = LocaleSettingRequest.model_fields["locale"].metadata
        # Field(pattern=...) 把约束放进 metadata 的 annotated_types 里
        found = None
        for meta in pattern:
            candidate = getattr(meta, "pattern", None)
            if candidate:
                found = candidate
                break
        assert found, "LocaleSettingRequest.locale 上没有 pattern 约束"

        for locale in i18n.SUPPORTED_LOCALES:
            assert re.match(found, locale), f"pattern {found!r} 不接受受支持的语言 {locale!r}"
        assert not re.match(found, "klingon")
