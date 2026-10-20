"""英文提示词路径的测试（i18n 阶段 4）

## 这个文件要守住什么

阶段 4 的改动有一条硬约束：**中文提示词一个字符都不能动**
（`test_prompt_golden.py` 的 11 个 sha256 是证据）。所以这里不重复测中文，
只测**新增的英文路径**与**两条路径的一致性**。

## 三类断言

1. **英文路径真的产出英文**：`locale="en"` 时 system 消息里没有 CJK 字符。
   （不断言"内容好看"，只断言"不是中文" —— 那才是可判定的性质。）
2. **JSON 契约不变**：英文提示词必须仍然要求模型返回同一组字段名与同一组
   `card_type` 枚举值。这两样是**代码标识符**，译了会让
   `structured.validate_items()` 解析失败 —— 这是英文提示词最容易犯的错。
3. **locale 的来源链**：显式参数 > `llm_context` > 默认中文。
"""

from __future__ import annotations

import re
from typing import List

import pytest

from app.core.i18n import DEFAULT_LOCALE
from app.services.llm import prompts
from app.services.llm_accounting_service import llm_context

CJK = re.compile(r"[\u4e00-\u9fff]")


# ---------------------------------------------------------------------------
# 1. 英文路径真的产出英文
# ---------------------------------------------------------------------------

def _message_functions() -> List[tuple[str, list]]:
    """(名字, 参数) —— 每个消息函数一份固定的调用参数

    ## ⚠️ 参数值必须用**语言中立**的内容（英文）

    因为 `types_str` / `cards_text` 这类参数会被**插值进 system 提示词**，
    若传中文进去，`test_english_system_message_has_no_cjk` 会把
    "调用方自己塞进去的中文"误判成"英文提示词里还有中文" ——
    实测踩过：传 `types_str="选择题"` 时该测试报红，而英文模板本身是干净的。

    所以这里一律用英文参数值：这个文件要测的是**模板**，
    不是"用户数据是不是中文"（后者本来就该原样透传）。
    """
    return [
        ("summarize_chapter_messages", ["Chapter 1", "body text"]),
        ("extract_knowledge_points_messages", ["Chapter 1", "body text"]),
        ("generate_questions_messages", ["Float charging", "content", "concept", "multiple-choice questions"]),
        ("generate_questions_batch_messages", ["- [1] Float charging", "multiple-choice questions", 2]),
        ("rag_answer_messages", ["What is float charging?", "[1] Float charging is an operating mode"]),
        ("generate_extension_knowledge_messages", ["Float charging", "content", "material"]),
        (
            "infer_card_relations_messages",
            [[{"id": "c1", "title": "Float charging", "card_type": "concept", "content": "content"}]],
        ),
        ("grade_short_answer_messages", ["question", "expected answer", "student answer"]),
    ]


@pytest.mark.parametrize("name,args", _message_functions(), ids=lambda v: v if isinstance(v, str) else "")
def test_english_system_message_has_no_cjk(name, args):
    """★ `locale="en"` 时 system 消息里不该出现中日韩字符"""
    messages = getattr(prompts, name)(*args, locale="en")
    system = next(m["content"] for m in messages if m["role"] == "system")
    found = CJK.findall(system)
    assert not found, f"{name} 的英文 system 提示词里仍有中文：{''.join(found[:20])}"


@pytest.mark.parametrize("name,args", _message_functions(), ids=lambda v: v if isinstance(v, str) else "")
def test_chinese_is_still_the_default(name, args):
    """不传 locale 且无上下文时，必须与显式 `locale="zh-CN"` 完全一致"""
    default = getattr(prompts, name)(*args)
    explicit = getattr(prompts, name)(*args, locale=DEFAULT_LOCALE)
    assert default == explicit


@pytest.mark.parametrize(
    "name", ["understanding_session", "question_session", "combined_analysis_session"]
)
def test_english_session_system_prompt_has_no_cjk(name):
    """会话类的三个系统提示词同样要能取到英文"""
    en = prompts.system_prompt_for(name, locale="en")
    assert not CJK.search(en), f"{name} 的英文系统提示词里仍有中文"
    zh = prompts.system_prompt_for(name, locale="zh-CN")
    assert CJK.search(zh), f"{name} 的中文系统提示词不应该是英文"


# ---------------------------------------------------------------------------
# 2. JSON 契约不变（英文提示词最容易在这里出错）
# ---------------------------------------------------------------------------

#: 每个提示词**必须**提到的字段名 —— 这些是 `structured.py` 解析时依赖的键，
#: 译成英文会让解析失败（模型会照提示词里的键名返回）
REQUIRED_KEYS = {
    "summarize_chapter": [],
    "extract_knowledge_points": ["card_type", "title", "content", "source_text"],
    "understanding_session": ["chapters", "chapter_title", "summary", "points"],
    "generate_questions": [
        "question_type", "difficulty", "question", "answer", "options", "explanation",
    ],
    "generate_questions_batch": ["questions", "card_index"],
    "question_session": ["questions", "card_index"],
    "rag_answer": [],
    "combined_analysis_session": ["chapter_title", "regular_points", "blind_spots", "is_key_point", "is_difficulty"],
    "generate_extension_knowledge": ["extensions", "card_type", "title", "content", "source_text"],
    "infer_card_relations": ["relations", "card_id_a", "card_id_b", "relation_type", "reason"],
    "grade_short_answer": ["verdict", "missing_points", "misconceptions", "confidence", "reason"],
}

#: 必须原样保留的**枚举值**（代码里的标识符，绝不能译）
REQUIRED_ENUMS = [
    "concept", "formula", "qa", "definition",
    "choice", "fill_blank", "short_answer",
    "easy", "medium", "hard",
    "prerequisite", "subsequent", "contrast",
    "correct", "partial", "incorrect",
]


def test_every_prompt_name_has_english():
    """★ 新增提示词时忘了写英文，必须在这里失败"""
    expected = set(prompts._ZH_SYSTEM_PROMPTS) | {
        "summarize_chapter",
        "extract_knowledge_points",
        "generate_questions",
        "generate_questions_batch",
        "rag_answer",
        "generate_extension_knowledge",
        "infer_card_relations",
        "grade_short_answer",
    }
    missing = expected - set(prompts._EN_PROMPTS)
    assert not missing, f"这些提示词没有英文版：{sorted(missing)}"


def test_prompt_versions_cover_every_prompt():
    """`PROMPT_VERSIONS` 与提示词集合必须一一对应（新增时两处都要登记）"""
    expected = set(prompts._ZH_SYSTEM_PROMPTS) | set(prompts._EN_PROMPTS)
    expected -= {"extract_knowledge_points"}  # 这个走非会话路径，但仍有版本号
    missing = expected - set(prompts.PROMPT_VERSIONS)
    assert not missing, f"这些提示词没登记版本号：{sorted(missing)}"


@pytest.mark.parametrize("name", sorted(REQUIRED_KEYS))
def test_english_prompt_keeps_json_keys(name):
    """英文提示词必须仍然提到同一组 JSON 字段名"""
    en = prompts._EN_PROMPTS[name]
    for key in REQUIRED_KEYS[name]:
        assert key in en, f"{name} 的英文提示词里没有提到字段 `{key}` —— 模型会返回别的键名"


@pytest.mark.parametrize("name", sorted(REQUIRED_KEYS))
def test_english_prompt_keeps_enum_values(name):
    """枚举值一律不译：它们是 `validate_items` 依赖的标识符"""
    en = prompts._EN_PROMPTS[name]
    for value in REQUIRED_ENUMS:
        if value in prompts._EN_PROMPTS[name]:
            continue  # 该提示词本来就不涉及这个枚举
    # 只要提示词里出现过某个枚举，英文版也必须用同一个写法
    zh = prompts._ZH_SYSTEM_PROMPTS.get(name, "")
    for value in REQUIRED_ENUMS:
        if value in zh or (name in REQUIRED_KEYS and value in str(REQUIRED_KEYS[name])):
            assert value in en, f"{name} 的英文版丢了枚举值 `{value}`"


def test_english_prompts_use_english_example_values():
    """示例值该译的译了（`"章节标题"` → `"chapter title"`），但键名不译"""
    understanding = prompts._EN_PROMPTS["understanding_session"]
    assert '"chapter_title": "chapter title"' in understanding
    assert "章节标题" not in understanding


# ---------------------------------------------------------------------------
# 3. locale 的来源链：显式参数 > llm_context > 默认中文
# ---------------------------------------------------------------------------

def test_explicit_locale_beats_context():
    """显式传的 locale 优先于上下文"""
    with llm_context(locale="en"):
        messages = prompts.summarize_chapter_messages("t", "c", locale="zh-CN")
    system = messages[0]["content"]
    assert CJK.search(system), "显式 zh-CN 应当压过上下文的 en"


def test_context_locale_is_used():
    """★ 上下文里声明的 locale 会被提示词层读到（worker 就是靠这条）"""
    with llm_context(locale="en"):
        messages = prompts.summarize_chapter_messages("t", "c")
    assert not CJK.search(messages[0]["content"])


def test_context_locale_reaches_session_prompts():
    with llm_context(locale="en"):
        en = prompts.system_prompt_for("understanding_session")
    assert not CJK.search(en)


def test_locale_normalizes_variants():
    """`en-US` / `EN` 都要落到 `en`（与 `core.i18n.normalize_locale` 一致）"""
    for variant in ("en-US", "EN", "en_GB"):
        with llm_context(locale=variant):
            messages = prompts.summarize_chapter_messages("t", "c")
        assert not CJK.search(messages[0]["content"]), f"{variant} 没有落到英文"


def test_unknown_locale_falls_back_to_default():
    """★ 认不出的语言回落默认中文，**不抛错** —— 语言不值得让任务失败"""
    with llm_context(locale="fr-FR"):
        messages = prompts.summarize_chapter_messages("t", "c")
    assert CJK.search(messages[0]["content"])


def test_context_locale_inherits_when_nested():
    """嵌套时内层不传 locale 会继承外层（与 user_id 同一规则）"""
    with llm_context(locale="en"):
        with llm_context(note_id="n1"):
            from app.services.llm_accounting_service import current_context

            assert current_context().locale == "en"


def test_context_locale_can_be_overridden_inner():
    with llm_context(locale="en"):
        with llm_context(locale="zh-CN"):
            from app.services.llm_accounting_service import current_context

            assert current_context().locale == "zh-CN"


def test_context_is_reset_after_exit():
    """退出后不该残留（否则会串到下一个请求/任务）"""
    from app.services.llm_accounting_service import current_context

    with llm_context(locale="en"):
        pass
    assert current_context().locale is None
