"""LLM 提示词模板（overhaul-plan 阶段 4.1 收尾）

## 这个文件为什么独立存在

搬移之前，提示词与调用逻辑、解析逻辑挤在 `llm_service.py` 的同一个方法里。
结果有两类问题：

1. **改提示词要读懂一整个方法**：想调整"出题时不要超纲"这一条，
   得先在 90 行的 `generate_questions` 里找到那段字符串；
2. **提示词无法被整体审视**：没有人能一眼看出产品对模型说了什么，
   而这是这个产品最核心的"业务逻辑"。

现在所有提示词都在这里，`LLMService` 只负责"什么时候问、拿到结果怎么解析"。

## ⚠️ 改动这里的文字会让响应缓存**全部失效**

阶段 4.7 的缓存键是完整输入的 sha256（messages 的每一个字符都在内）。
改提示词本身没有问题 —— 问题是**顺手**改：那会让命中率一夜归零，
而表现只是"缓存好像没生效"，不会报任何错。

因此 `tests/test_prompt_golden.py` 固化了每个场景送给模型的输入的摘要。
**有意**改提示词时，请连同那 11 个摘要一起更新（并在提交信息里说明原因）。

## 为什么有的场景是"函数"、有的是"常量"

- 需要在文本里嵌入数据的（章节标题、用户问题、卡片内容）→ 函数；
- 固定不变的 → 模块级常量（名字统一以 `_SYSTEM_PROMPT` 结尾）。

## 唯一一处与"逐字节搬移"不同的地方

`generate_questions_batch_messages` 原来用 `len(cards)` 内联计算题目数量，
搬移后改为参数 `card_count`（函数不该知道 `cards` 这个列表的存在）。
**拼出来的文本完全相同** —— 由 test_prompt_golden 的摘要证明。
"""

from typing import Any, Dict, List, Optional

from ...core.i18n import DEFAULT_LOCALE, normalize_locale


def _resolve_locale(locale: Optional[str] = None) -> str:
    """决定这次提示词用哪种语言

    ## 三级回落（与前端 `t()` 的三级回落同构）

    1. 显式传入的 `locale`（调用方明确指定）；
    2. `llm_context` 里声明的 `locale`（worker 从 `users.locale` 读出来后声明）；
    3. `core.i18n.DEFAULT_LOCALE`（中文）。

    ## 为什么默认是"读上下文"而不是"默认中文"

    因为**绝大多数调用点不该关心语言**：语言是"谁在用"的属性，
    不是"这一步在做什么"的属性。默认读上下文意味着
    `summarize_chapter(...)` 这类调用一个字都不用改，而 worker 只在
    最外层声明一次 `llm_context(locale=...)` 即可（与 `user_id` 同一模式）。

    ## 非法值怎么办

    交给 `normalize_locale()`：它把 `en-US` / `EN` 这类规范成 `en`，
    无法识别时回落 `DEFAULT_LOCALE`。**不抛错** —— 提示词语言不值得
    让一次理解任务失败。
    """
    if locale is None:
        # 延迟导入：`llm_accounting_service` 会 import 本模块之外的很多东西，
        # 顶层 import 会形成环（本模块被 llm 包内多处引用）。
        from ..llm_accounting_service import current_context

        locale = current_context().locale
    if locale is None:
        return DEFAULT_LOCALE
    return normalize_locale(locale)


# ---------------------------------------------------------------------------
# 英文提示词（i18n 阶段 4）
# ---------------------------------------------------------------------------
#
# ## ⚠️ 中文提示词一个字都没有动
#
# `tests/test_prompt_golden.py` 固化了每个场景送给模型的输入的 sha256，
# 用来证明"搬移提示词时没改一个字符"。这次加英文**没有**动中文的任何一个
# 字符，所以那 11 个摘要**保持不变** —— 这不是巧合，是刻意：
#
# - 中文用户的响应缓存不会失效（缓存键是完整输入的 sha256）；
# - golden 测试仍然证明"中文提示词自搬移以来逐字节未变"。
#
# 若哪天**有意**改中文提示词，才需要同时更新 GOLDEN 与 `PROMPT_VERSIONS`。
#
# ## 英文不是"逐字直译"
#
# 几处刻意不同：
#
# - **JSON 里的示例值仍用中文**（如 `"章节标题"` → `"chapter title"` 改成英文，
#   但 `card_type` 的**枚举值** `concept/formula/qa/definition` 一律不译 ——
#   它们是代码里的标识符，译了会让 `validate_items` 解析失败）；
# - `rag_answer` 的「资料中没有找到相关信息」译成英文时**必须与前端
#   `qa` 场景的展示逻辑对齐**：那句是给用户看的固定话术，不是随便一句话。
_EN_PROMPTS: Dict[str, str] = {
    # ---- 章节摘要 ----
    "summarize_chapter": (
        "You are a professional academic assistant. Produce a concise and accurate "
        "summary of the given chapter. The summary should cover: 1) the chapter's core "
        "topic, 2) the key arguments or findings, 3) the important conclusions. "
        "Keep the summary under 200 words."
    ),
    # ---- 单章节知识点提取 ----
    "extract_knowledge_points": (
        "You are a professional knowledge-extraction assistant. Extract the key "
        "knowledge points from the given chapter.\n\n"
        "Knowledge point types:\n"
        "- concept: conceptual knowledge that must be understood and remembered\n"
        "- formula: formulas, chemical equations, and the like\n"
        "- qa: question-and-answer pairs\n"
        "- definition: definitions that must be remembered precisely\n\n"
        "Return a JSON array where each element contains:\n"
        "- card_type: one of concept/formula/qa/definition\n"
        "- title: a concise title for the knowledge point\n"
        "- content: a detailed description of the knowledge point\n"
        "- source_text: the passage in the original text it comes from\n\n"
        "Requirements:\n"
        "1. Each knowledge point must be self-contained and understandable on its own\n"
        "2. source_text should quote the original text as closely as possible\n"
        "3. Extract 5-15 knowledge points\n"
        "4. Return only the JSON array, with no other text"
    ),
    # ---- 多轮知识提取会话 ----
    "understanding_session": (
        "You are a professional knowledge-extraction assistant. I will give you the "
        "content of one or more chapters at a time; for each chapter, produce a summary "
        "and extract the key knowledge points.\n\n"
        "Knowledge point types:\n"
        "- concept: conceptual knowledge that must be understood and remembered\n"
        "- formula: formulas, chemical equations, and the like\n"
        "- qa: question-and-answer pairs\n"
        "- definition: definitions that must be remembered precisely\n\n"
        "Return strictly in the following JSON format (add no other text):\n"
        '{"chapters": [\n'
        '  {\n'
        '    "chapter_title": "chapter title",\n'
        '    "summary": "chapter summary (under 200 words)",\n'
        '    "points": [\n'
        '      {"card_type": "concept", "title": "knowledge point title", '
        '"content": "knowledge point content", "source_text": "quoted source text"},\n'
        '      ...\n'
        '    ]\n'
        '  },\n'
        '  ...\n'
        "]}\n\n"
        "Requirements:\n"
        "1. Each knowledge point must be self-contained and understandable on its own\n"
        "2. source_text should quote the original text as closely as possible\n"
        "3. Extract 5-15 knowledge points per chapter\n"
        "4. Do not repeat knowledge points extracted earlier\n"
        "5. Return only the JSON object, with no other text"
    ),
    # ---- 单卡片出题 ----
    "generate_questions": (
        "You are a professional question-writing assistant. Generate $types_str "
        "based on the given knowledge point.\n\n"
        "Return a JSON array where each element contains:\n"
        "- question_type: one of choice/fill_blank/short_answer\n"
        "- difficulty: one of easy/medium/hard\n"
        "- question: the question text\n"
        "- answer: the correct answer\n"
        "- options: the choices (only for multiple-choice; a JSON array such as "
        '["A. option 1", "B. option 2", "C. option 3", "D. option 4"]; null otherwise)\n'
        "- explanation: why this answer is correct\n\n"
        "Requirements:\n"
        "1. Questions must test the knowledge point accurately and stay within the given content\n"
        "2. Distractors in multiple-choice questions must be plausible, not obviously wrong\n"
        "3. Generate 1-2 questions of each type\n"
        "4. Return only the JSON array, with no other text"
    ),
    # ---- 批量出题 ----
    "generate_questions_batch": (
        "You are a professional question-writing assistant. Based on the "
        "$card_count knowledge points given, generate 1 $types_str for each "
        "knowledge point.\n\n"
        "Return strictly in the following JSON format (add no other text):\n"
        '{"questions": [\n'
        '  {"card_index": 1, "question_type": "choice", "difficulty": "easy", '
        '"question": "question text", "answer": "correct answer", '
        '"options": ["A.option 1", "B.option 2", "C.option 3", "D.option 4"], '
        '"explanation": "explanation"},\n'
        '  {"card_index": 2, ...}\n'
        ']}\n\n'
        "Requirements:\n"
        "1. Generate 1 question per knowledge point, $card_count questions in total\n"
        "2. Questions must test the knowledge point accurately and stay within the given content\n"
        "3. Distractors in multiple-choice questions must be plausible\n"
        "4. card_index starts at 1 and corresponds to the knowledge point number\n"
        "5. options must be an array of strings, each formatted as 'letter.content'\n"
        "6. Return only the JSON object, with no other text"
    ),
    # ---- 多轮出题会话 ----
    "question_session": (
        "You are a professional question-writing assistant. I will give you several "
        "groups of knowledge points in turn; generate multiple-choice questions for "
        "each group.\n\n"
        "Return strictly in the following JSON format (add no other text):\n"
        '{"questions": [\n'
        '  {"card_index": 1, "question_type": "choice", "difficulty": "easy", '
        '"question": "question text", "answer": "correct answer", '
        '"options": ["A.option 1", "B.option 2", "C.option 3", "D.option 4"], '
        '"explanation": "explanation"},\n'
        '  {"card_index": 2, ...}\n'
        "]}\n\n"
        "Requirements:\n"
        "1. Generate 1 multiple-choice question per knowledge point\n"
        "2. Questions must test the knowledge point accurately and stay within the given content\n"
        "3. Distractors in multiple-choice questions must be plausible\n"
        "4. card_index starts at 1 and corresponds to the number within this group\n"
        "5. options must be an array of strings, each formatted as 'letter.content'\n"
        "6. Do not repeat or closely resemble questions generated earlier\n"
        "7. Return only the JSON object, with no other text"
    ),
    # ---- RAG 问答 ----
    "rag_answer": (
        "You are a rigorous study assistant. Your answer may draw **only** on the "
        "reference material provided below.\n\n"
        "Hard rules (you must follow these):\n"
        "1. **Reference material only**: do not use any knowledge beyond the reference "
        "material, even if you are confident it is correct.\n"
        "2. **Say so when there is no basis**: if the reference material does not contain "
        "enough information to answer, reply directly with "
        '"No relevant information was found in the material." and explain what the '
        "material does cover. **Do not guess, do not fill in, do not generalize.**\n"
        "3. **Cite every claim**: after each conclusion, mark which passage it comes from "
        "with [n], where n is the passage number in the reference material.\n"
        "4. **Report faithfully**: do not change the meaning, and do not omit conditions, "
        "scopes or premises. If the material contradicts itself, point out the "
        "contradiction instead of deciding for the user.\n"
        "5. **Separate fact from inference**: if a sentence is your inference rather than "
        'the material\'s own words, mark it explicitly with "(inferred)".\n\n'
        "Answer format: answer the question directly first, then list the passage numbers "
        "you relied on."
    ),
    # ---- 联合分析会话 ----
    "combined_analysis_session": (
        "You are a professional study-analysis assistant. I will give you each chapter "
        "of the study material together with the user's complete notes; perform a "
        "combined analysis for each chapter.\n\n"
        "Knowledge point types:\n"
        "- concept: conceptual knowledge that must be understood and remembered\n"
        "- formula: formulas, chemical equations, and the like\n"
        "- qa: question-and-answer pairs\n"
        "- definition: definitions that must be remembered precisely\n\n"
        "Return strictly in the following JSON format (add no other text):\n"
        "{\n"
        '  "chapter_title": "chapter title",\n'
        '  "regular_points": [\n'
        '    {"card_type": "concept", "title": "knowledge point title", '
        '"content": "knowledge point content", '
        '"source_text": "quoted source text", "is_key_point": false, "is_difficulty": false}\n'
        "  ],\n"
        '  "blind_spots": [\n'
        '    {"card_type": "concept", "title": "blind spot title", '
        '"content": "blind spot content", '
        '"source_text": "quoted source text", "is_key_point": false, "is_difficulty": false}\n'
        "  ]\n"
        "}\n\n"
        "Requirements:\n"
        "1. regular_points: knowledge points covered by both the material and the user's notes\n"
        "2. blind_spots: knowledge points present in the material but missing from the user's notes\n"
        "3. is_key_point/is_difficulty: suggest true/false based on importance and difficulty\n"
        "4. Each knowledge point must be self-contained and understandable on its own\n"
        "5. source_text should quote the material as closely as possible\n"
        "6. Do not repeat knowledge points extracted earlier\n"
        "7. Return only the JSON object, with no other text"
    ),
    # ---- 拓展知识点 ----
    "generate_extension_knowledge": (
        "You are a professional knowledge-expansion assistant. I will give you a "
        "knowledge point the user has already mastered together with related material; "
        "generate 1-3 advanced extension knowledge points.\n\n"
        "Return strictly in the following JSON format (add no other text):\n"
        "{\n"
        '  "extensions": [\n'
        '    {"card_type": "concept", "title": "extension title", '
        '"content": "extension content", "source_text": "quoted source text or empty"}\n'
        "  ]\n"
        "}\n\n"
        "Requirements:\n"
        "1. Extensions must go further than, relate to, or deepen the original knowledge point\n"
        "2. Each knowledge point must be self-contained\n"
        "3. Return only the JSON object, with no other text"
    ),
    # ---- 卡片关系推断 ----
    "infer_card_relations": (
        "You are a professional knowledge-graph assistant. I will give you summaries "
        "of several knowledge cards; infer the semantic relations between them.\n\n"
        "Relation types:\n"
        "- prerequisite: card_a is a prerequisite for card_b (you must learn a to understand b)\n"
        "- subsequent: card_a is a follow-up to card_b (a extends b)\n"
        "- contrast: the two cards contrast with each other\n\n"
        "Return strictly in the following JSON format (add no other text):\n"
        "{\n"
        '  "relations": [\n'
        '    {"card_id_a": "card id 1", "card_id_b": "card id 2", '
        '"relation_type": "prerequisite", "reason": "why you inferred this"}\n'
        "  ]\n"
        "}\n\n"
        "Requirements:\n"
        "1. Only infer relations that genuinely exist; do not force connections\n"
        "2. relation_type must be one of prerequisite/subsequent/contrast\n"
        "3. card_id_a and card_id_b must be ids from the given card list\n"
        "4. At most one relation per pair of cards\n"
        "5. Return only the JSON object, with no other text"
    ),
    # ---- 简答判分 ----
    "grade_short_answer": (
        "You are a strict examiner judging whether a student's **short answer** is "
        "semantically equivalent to the reference answer.\n\n"
        "Judge **semantic equivalence only**: do not bring in knowledge beyond the "
        "reference answer, and do not mark an answer wrong just because the wording differs.\n\n"
        "Return strictly in the following JSON format (add no other text):\n"
        '{"verdict": "correct|partial|incorrect",\n'
        ' "missing_points": ["key points the student missed"],\n'
        ' "misconceptions": ["statements that contradict the reference answer"],\n'
        ' "confidence": 0.0,\n'
        ' "reason": "one sentence explaining the verdict"}\n\n'
        "Criteria:\n"
        "1. correct: the core meaning matches the reference answer. Different wording, "
        "different order, more brevity or more detail are all **allowed**\n"
        "2. partial: partly right, but with clear omissions or imprecision\n"
        "3. incorrect: contradicts the reference answer, or answers something else\n\n"
        "confidence must honestly reflect how sure you are:\n"
        "- Give a high score (>=0.8) when the meanings are clearly the same or clearly different\n"
        "- Give a low score when the student's answer is outside the reference answer but "
        "may be equally correct, or when the wording is too vague to judge\n"
        "- **Do not** inflate confidence just to sound certain"
    ),
}


def system_prompt_for(name: str, locale: Optional[str] = None) -> str:
    """按语言取**会话类**的系统提示词

    `name` 取 `summarize_chapter` 之外的那几个会话名（`understanding_session`
    / `question_session` / `combined_analysis_session`）。

    ⚠️ 中文分支返回的是**模块级常量本身**（不是重新拼一遍字符串）——
    这样 `UNDERSTANDING_SYSTEM_PROMPT` 这类公开名字仍然是权威定义，
    golden 测试固化的也是同一个对象的内容。
    """
    resolved = _resolve_locale(locale)
    if resolved == "en":
        return _EN_PROMPTS[name]
    return _ZH_SYSTEM_PROMPTS[name]



def summarize_chapter_messages(chapter_title: str, chapter_content: str, locale: Optional[str] = None) -> List[Dict[str, str]]:
    """章节摘要"""
    if _resolve_locale(locale) == "en":
        return [
            {"role": "system", "content": _EN_PROMPTS["summarize_chapter"]},
            {
                "role": "user",
                "content": f"Chapter title: {chapter_title}\n\nChapter content:\n{chapter_content}",
            },
        ]
    return [
        {
            "role": "system",
            "content": (
                "你是一个专业的学术助手。请为给定章节生成简洁准确的摘要。"
                "摘要应包含：1) 章节核心主题 2) 关键论点或发现 3) 重要结论。"
                "摘要长度控制在200字以内。"
            ),
        },
        {
            "role": "user",
            "content": f"章节标题：{chapter_title}\n\n章节内容：\n{chapter_content}",
        },
    ]


def extract_knowledge_points_messages(chapter_title: str, chapter_content: str, locale: Optional[str] = None) -> List[Dict[str, str]]:
    """单章节知识点提取（非会话路径）"""
    if _resolve_locale(locale) == "en":
        return [
            {"role": "system", "content": _EN_PROMPTS["extract_knowledge_points"]},
            {
                "role": "user",
                "content": f"Chapter title: {chapter_title}\n\nChapter content:\n{chapter_content}",
            },
        ]
    return [
        {
            "role": "system",
            "content": (
                "你是一个专业的知识提取助手。请从给定章节中提取关键知识点。\n\n"
                "知识点类型说明：\n"
                "- concept: 概念类，需要理解记忆的知识点\n"
                "- formula: 公式类，数学公式、化学方程式等\n"
                "- qa: 问答对，以问答形式呈现的知识\n"
                "- definition: 定义类，需要精确记忆的定义\n\n"
                "请以 JSON 数组格式返回，每个元素包含：\n"
                '- card_type: 类型（concept/formula/qa/definition）\n'
                "- title: 知识点标题（简洁明了）\n"
                "- content: 知识点内容（详细描述）\n"
                "- source_text: 原始出处文本（原文中对应的段落）\n\n"
                "要求：\n"
                "1. 每个知识点应独立完整，不依赖上下文也能理解\n"
                "2. source_text 应尽量引用原文\n"
                "3. 提取5-15个知识点\n"
                "4. 只返回 JSON 数组，不要其他文字"
            ),
        },
        {
            "role": "user",
            "content": f"章节标题：{chapter_title}\n\n章节内容：\n{chapter_content}",
        },
    ]


#: 多轮知识提取会话的系统提示词
UNDERSTANDING_SYSTEM_PROMPT = (
    "你是一个专业的知识提取助手。我将一次给你一个或多个章节的内容，"
    "请为每个章节生成摘要并提取关键知识点。\n\n"
    "知识点类型说明：\n"
    "- concept: 概念类，需要理解记忆的知识点\n"
    "- formula: 公式类，数学公式、化学方程式等\n"
    "- qa: 问答对，以问答形式呈现的知识\n"
    "- definition: 定义类，需要精确记忆的定义\n\n"
    "请严格按以下 JSON 格式返回（不要添加任何其他文字）：\n"
    '{"chapters": [\n'
    '  {\n'
    '    "chapter_title": "章节标题",\n'
    '    "summary": "章节摘要（200字以内）",\n'
    '    "points": [\n'
    '      {"card_type": "concept", "title": "知识点标题", '
    '"content": "知识点内容", "source_text": "原始出处文本"},\n'
    '      ...\n'
    '    ]\n'
    '  },\n'
    '  ...\n'
    "]}\n\n"
    "要求：\n"
    "1. 每个知识点应独立完整，不依赖上下文也能理解\n"
    "2. source_text 应尽量引用原文\n"
    "3. 每个章节提取5-15个知识点\n"
    "4. 不要与之前已提取的知识点重复\n"
    "5. 只返回 JSON 对象，不要其他文字"
)


def generate_questions_messages(card_title: str, card_content: str, card_type: str, types_str: str, locale: Optional[str] = None) -> List[Dict[str, str]]:
    """单卡片出题"""
    if _resolve_locale(locale) == "en":
        return [
            {
                "role": "system",
                # ⚠️ 不能用 `str.format()`：英文提示词里有 JSON 字面量
                # （`{"questions": [...]}`），`{` 会被当成占位符并抛
                # `KeyError: '"questions"'`。用显式 replace 最直白。
                "content": _EN_PROMPTS["generate_questions"].replace("$types_str", types_str),
            },
            {
                "role": "user",
                "content": (
                    f"Knowledge point title: {card_title}\n"
                    f"Knowledge point type: {card_type}\n"
                    f"Knowledge point content: {card_content}"
                ),
            },
        ]
    return [
        {
            "role": "system",
            "content": (
                f"你是一个专业的出题助手。请根据给定知识点生成{types_str}。\n\n"
                "请以 JSON 数组格式返回，每个元素包含：\n"
                '- question_type: 题目类型（choice/fill_blank/short_answer）\n'
                "- difficulty: 难度（easy/medium/hard）\n"
                "- question: 题目内容\n"
                "- answer: 正确答案\n"
                "- options: 选择题选项（仅选择题需要，JSON 数组格式，如 "
                '[\"A. 选项1\", \"B. 选项2\", \"C. 选项3\", \"D. 选项4\"]，其他类型为 null）\n'
                "- explanation: 题目解析（解释为什么这个答案是对的）\n\n"
                "要求：\n"
                "1. 题目应准确考察知识点，不超出给定内容范围\n"
                "2. 选择题的干扰项应合理，不能明显错误\n"
                "3. 每种类型生成1-2道题\n"
                "4. 只返回 JSON 数组，不要其他文字"
            ),
        },
        {
            "role": "user",
            "content": (
                f"知识点标题：{card_title}\n"
                f"知识点类型：{card_type}\n"
                f"知识点内容：{card_content}"
            ),
        },
    ]


def generate_questions_batch_messages(cards_text: str, types_str: str, card_count: int, locale: Optional[str] = None) -> List[Dict[str, str]]:
    """批量出题：一次请求里为多个知识点各出一道题"""
    if _resolve_locale(locale) == "en":
        return [
            {
                "role": "system",
                # 同上：不能 `str.format()`（JSON 字面量里的 `{` 会炸）
                "content": _EN_PROMPTS["generate_questions_batch"]
                .replace("$types_str", types_str)
                .replace("$card_count", str(card_count)),
            },
            {
                "role": "user",
                "content": cards_text,
            },
        ]
    return [
        {
            "role": "system",
            "content": (
                f"你是一个专业的出题助手。请根据给定的 {card_count} 个知识点，"
                f"为每个知识点生成1道{types_str}。\n\n"
                '请严格按以下 JSON 格式返回（不要添加任何其他文字）：\n'
                '{"questions": [\n'
                '  {"card_index": 1, "question_type": "choice", "difficulty": "easy", '
                '"question": "题目内容", "answer": "正确答案", '
                '"options": ["A.选项1", "B.选项2", "C.选项3", "D.选项4"], '
                '"explanation": "解析"},\n'
                '  {"card_index": 2, ...}\n'
                ']}\n\n'
                "要求：\n"
                "1. 每个知识点生成1道题，共生成" + str(card_count) + "道题\n"
                "2. 题目应准确考察知识点，不超出给定内容范围\n"
                "3. 选择题的干扰项应合理\n"
                "4. card_index 从1开始，对应知识点编号\n"
                "5. options 必须是字符串数组，每个元素格式为 '字母.内容'\n"
                "6. 只返回 JSON 对象，不要其他文字"
            ),
        },
        {
            "role": "user",
            "content": cards_text,
        },
    ]


#: 多轮出题会话的系统提示词
QUESTION_SYSTEM_PROMPT = (
    "你是一个专业的出题助手。我将依次给你多组知识点，"
    "请为每组知识点生成选择题。\n\n"
    "请严格按以下 JSON 格式返回（不要添加任何其他文字）：\n"
    '{"questions": [\n'
    '  {"card_index": 1, "question_type": "choice", "difficulty": "easy", '
    '"question": "题目内容", "answer": "正确答案", '
    '"options": ["A.选项1", "B.选项2", "C.选项3", "D.选项4"], '
    '"explanation": "解析"},\n'
    '  {"card_index": 2, ...}\n'
    "]}\n\n"
    "要求：\n"
    "1. 每个知识点生成1道选择题\n"
    "2. 题目应准确考察知识点，不超出给定内容范围\n"
    "3. 选择题的干扰项应合理\n"
    "4. card_index 从1开始，对应本组知识点编号\n"
    "5. options 必须是字符串数组，每个元素格式为 '字母.内容'\n"
    "6. 不要与之前已生成的题目重复或雷同\n"
    "7. 只返回 JSON 对象，不要其他文字"
)


def rag_answer_messages(question: str, context: str, locale: Optional[str] = None) -> List[Dict[str, str]]:
    """RAG 问答：**只依据给定资料**回答（阶段 2.8 重写，见函数内说明）"""
    if _resolve_locale(locale) == "en":
        return [
            {"role": "system", "content": _EN_PROMPTS["rag_answer"]},
            {
                "role": "user",
                "content": f"Reference material:\n{context}\n\nQuestion: {question}",
            },
        ]
    return [
        {
            "role": "system",
            "content": (
                "你是一个严谨的学习助手。你的回答**只能**依据下面提供的参考资料。\n\n"
                "硬性规则（必须遵守）：\n"
                "1. **只依据参考资料**：不得使用参考资料之外的任何知识，"
                "即使你确信那些知识是正确的。\n"
                "2. **无据则明说**：如果参考资料中没有足以回答问题的信息，"
                "直接回答「资料中没有找到相关信息」，并说明资料里实际涵盖了"
                "什么。**不要猜测、不要补充、不要泛泛而谈**。\n"
                "3. **逐条标注来源**：每个结论后面用 [编号] 标注它来自哪段资料，"
                "编号对应参考资料中的段落序号。\n"
                "4. **忠实转述**：不得改变原意，不得把资料中的条件、范围、"
                "前提省略掉。若资料之间互相矛盾，指出矛盾而不是替用户裁决。\n"
                "5. **区分事实与推断**：如果某句是你的推断（而非资料原文），"
                "必须显式标注「（推断）」。\n\n"
                "回答格式：先直接回答问题，再列出依据的段落编号。"
            ),
        },
        {
            "role": "user",
            "content": f"参考资料：\n{context}\n\n问题：{question}",
        },
    ]


#: 联合分析（资料 + 用户笔记）的系统提示词
COMBINED_ANALYSIS_SYSTEM_PROMPT = (
    "你是一个专业的学习分析助手。我将依次给你学习资料的各个章节以及用户的完整笔记，请对每个章节做联合分析。\n\n"
    "知识点类型说明：\n"
    "- concept: 概念类，需要理解记忆的知识点\n"
    "- formula: 公式类，数学公式、化学方程式等\n"
    "- qa: 问答对，以问答形式呈现的知识\n"
    "- definition: 定义类，需要精确记忆的定义\n\n"
    "请严格按以下 JSON 格式返回（不要添加任何其他文字）：\n"
    "{\n"
    '  "chapter_title": "章节标题",\n'
    '  "regular_points": [\n'
    '    {"card_type": "concept", "title": "知识点标题", "content": "知识点内容", '
    '"source_text": "原始出处文本", "is_key_point": false, "is_difficulty": false}\n'
    "  ],\n"
    '  "blind_spots": [\n'
    '    {"card_type": "concept", "title": "盲点知识点标题", "content": "盲点内容", '
    '"source_text": "原始出处文本", "is_key_point": false, "is_difficulty": false}\n'
    "  ]\n"
    "}\n\n"
    "要求：\n"
    "1. regular_points：资料和用户笔记都覆盖到的知识点\n"
    "2. blind_spots：资料中有但用户笔记未覆盖到的知识点\n"
    "3. is_key_point/is_difficulty：根据知识点重要性和难度给出 true/false 建议\n"
    "4. 每个知识点应独立完整，不依赖上下文也能理解\n"
    "5. source_text 应尽量引用资料原文\n"
    "6. 不要与之前已提取的知识点重复\n"
    "7. 只返回 JSON 对象，不要其他文字"
)


#: 会话类系统提示词的**中文**权威表（i18n 阶段 4）
#:
#: 值是上面那几个模块级常量**本身**，不是重新拼的副本 ——
#: 这样"中文提示词只有一处定义"这条性质不被 i18n 破坏。
#: `system_prompt_for()` 按语言在这张表与 `_EN_PROMPTS` 之间选。
_ZH_SYSTEM_PROMPTS: Dict[str, str] = {
    "understanding_session": UNDERSTANDING_SYSTEM_PROMPT,
    "question_session": QUESTION_SYSTEM_PROMPT,
    "combined_analysis_session": COMBINED_ANALYSIS_SYSTEM_PROMPT,
}


def generate_extension_knowledge_messages(card_title: str, card_content: str, material_context: str, locale: Optional[str] = None) -> List[Dict[str, str]]:
    """进阶拓展知识点：系统提示词 + 用户消息"""
    if _resolve_locale(locale) == "en":
        return [
            {"role": "system", "content": _EN_PROMPTS["generate_extension_knowledge"]},
            {
                "role": "user",
                "content": (
                    f"## Mastered knowledge point:\nTitle: {card_title}\n"
                    f"Content: {card_content}\n\n"
                    f"## Related material:\n{material_context}\n\n"
                    f"Generate 1-3 advanced extension knowledge points."
                ),
            },
        ]
    system_prompt = (
        "你是一个专业的知识拓展助手。我将给你一个已掌握的知识点及其关联资料，请生成1-3个进阶拓展知识点。\n\n"
        "请严格按以下 JSON 格式返回（不要添加任何其他文字）：\n"
        "{\n"
        '  "extensions": [\n'
        '    {"card_type": "concept", "title": "拓展知识点标题", "content": "拓展知识点内容", '
        '"source_text": "原始出处文本或空"}\n'
        "  ]\n"
        "}\n\n"
        "要求：\n"
        "1. 拓展知识点应在原知识点基础上有进阶、关联或深化\n"
        "2. 每个知识点应独立完整\n"
        "3. 只返回 JSON 对象，不要其他文字"
    )
    user_prompt = (
        f"## 已掌握知识点：\n标题：{card_title}\n内容：{card_content}\n\n"
        f"## 关联资料：\n{material_context}\n\n"
        f"请生成1-3个进阶拓展知识点。"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    return messages


def infer_card_relations_messages(cards_summary: List[Dict[str, Any]], locale: Optional[str] = None) -> List[Dict[str, str]]:
    """卡片关系推断：系统提示词 + 卡片摘要文本 + 用户消息"""
    if _resolve_locale(locale) == "en":
        en_lines = [
            f"- [id={c['id']}] {c['title']} ({c['card_type']}): {c['content'][:300]}"
            for c in cards_summary
        ]
        return [
            {"role": "system", "content": _EN_PROMPTS["infer_card_relations"]},
            {"role": "user", "content": "\n".join(en_lines)},
        ]
    system_prompt = (
        "你是一个专业的知识图谱构建助手。我将给你若干知识卡片的摘要，请推断它们之间的语义关系。\n\n"
        "关系类型说明：\n"
        "- prerequisite: card_a 是 card_b 的前置知识（学 a 才能懂 b）\n"
        "- subsequent: card_a 是 card_b 的后续知识（b 的延伸是 a）\n"
        "- contrast: 两张卡片内容形成对比\n\n"
        "请严格按以下 JSON 格式返回（不要添加任何其他文字）：\n"
        "{\n"
        '  "relations": [\n'
        '    {"card_id_a": "卡片id1", "card_id_b": "卡片id2", '
        '"relation_type": "prerequisite", "reason": "推断理由"}\n'
        "  ]\n"
        "}\n\n"
        "要求：\n"
        "1. 只推断确实存在的关系，不要强行关联\n"
        "2. relation_type 必须是 prerequisite/subsequent/contrast 之一\n"
        "3. card_id_a 和 card_id_b 必须是给定卡片列表中的 id\n"
        "4. 每对卡片最多一种关系\n"
        "5. 只返回 JSON 对象，不要其他文字"
    )
    # 把 cards_summary 格式化为文本,每个卡片一行(content 截断到 300 字)
    cards_text_lines = []
    for c in cards_summary:
        cards_text_lines.append(
            f"- [id={c['id']}] {c['title']} ({c['card_type']}): {c['content'][:300]}"
        )
    user_prompt = "\n".join(cards_text_lines)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    return messages


def grade_short_answer_messages(question: str, expected_answer: str, user_answer: str, locale: Optional[str] = None) -> List[Dict[str, str]]:
    """简答判分：系统提示词 + 用户消息"""
    if _resolve_locale(locale) == "en":
        return [
            {"role": "system", "content": _EN_PROMPTS["grade_short_answer"]},
            {
                "role": "user",
                "content": (
                    f"Question: {question}\n\n"
                    f"Reference answer: {expected_answer}\n\n"
                    f"Student answer: {user_answer}"
                ),
            },
        ]
    system_prompt = (
        "你是一个严格的阅卷老师，负责判断学生的**简答题**作答在语义上"
        "是否与标准答案一致。\n\n"
        "只做**语义等价判断**：不要引入标准答案之外的知识，"
        "也不要因为表述风格不同就判错。\n\n"
        "请严格按以下 JSON 格式返回（不要添加任何其他文字）：\n"
        '{"verdict": "correct|partial|incorrect",\n'
        ' "missing_points": ["学生答案漏掉的关键点"],\n'
        ' "misconceptions": ["学生答案中与标准答案矛盾的说法"],\n'
        ' "confidence": 0.0,\n'
        ' "reason": "一句话说明判分理由"}\n\n'
        "判定标准：\n"
        "1. correct：核心含义与标准答案一致。**允许**措辞不同、"
        "语序不同、更简略或更详细\n"
        "2. partial：说对了部分内容，但有明显遗漏或不够准确\n"
        "3. incorrect：与标准答案矛盾，或答的是另一件事\n\n"
        "confidence 必须如实反映你的把握：\n"
        "- 含义明显一致或不一致时给高分（>=0.8）\n"
        "- 学生答案在标准答案之外但可能同样正确、或表述含糊难判时给低分\n"
        "- **不要**为了显得确定而虚报高置信度"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                f"题目：{question}\n\n"
                f"标准答案：{expected_answer}\n\n"
                f"学生作答：{user_answer}"
            ),
        },
    ]
    return messages


# ---------------------------------------------------------------------------
# 提示词版本（阶段 4.6：溯源）
# ---------------------------------------------------------------------------

#: 提示词名 → 版本。**每改一次提示词文本就必须改这里**（见下）
#:
#: ## 版本号解决的是什么问题
#:
#: 改了提示词之后，"新版是不是更好"只能靠数据回答：把卡片与题目按
#: `prompt_version` 分组，再看各组的复习表现。没有版本号时，表里混着
#: 不同提示词产出的行，任何分组比较都做不了 —— 只剩"感觉新版好一些"。
#:
#: ## 为什么是手工维护的字符串，而不是提示词文本的哈希
#:
#: 哈希看起来更省事（改文本自动变版本），但它**答不了业务问题**：
#: "第 3 版比第 2 版好吗"需要人能读懂、能排序、能在文档里引用的编号；
#: 而 `a3f9c1e2` 既不能排序，也无法在评审时说清"我们说的是哪一版"。
#:
#: 手工维护的代价是"可能忘记改"，这一条由测试兜住：
#: `tests/test_prompt_golden.py` 把每个提示词的**版本 + 输入摘要**一起固化，
#: 于是"文本变了但版本没变"会当场失败。忘记改不再是可能。
#:
#: ## 取值规则
#:
#: 从 "1" 开始的正整数（字符串形式）。**不相加字母后缀**：
#: `"1a"` 这种写法在实践中会分裂成两套排序规则，而版本号唯一的用途就是排序。
PROMPT_VERSIONS: Dict[str, str] = {
    "summarize_chapter": "1",
    "extract_knowledge_points": "1",
    "understanding_session": "1",
    "generate_questions": "1",
    "generate_questions_batch": "1",
    "question_session": "1",
    "rag_answer": "2",          # 阶段 2.8 重写为"无据不答"，是第 2 版
    "combined_analysis_session": "1",
    "generate_extension_knowledge": "1",
    "infer_card_relations": "1",
    "grade_short_answer": "1",  # 阶段 3.5 新增
}


def prompt_version(name: str) -> Optional[str]:
    """取提示词版本；名字未登记时返回 **None**（而不是猜一个）

    ⚠️ 未登记时返回 None 是有意的：调用方会把它写进 `prompt_version` 列，
    而 NULL 的含义是"未知"。返回 `"1"` 会让未知行伪装成第一版，
    从而污染按版本分组的统计 —— 与记账里"没配价格时 cost 记 NULL 而不是 0"
    是同一条原则。

    新增提示词时忘了登记，`tests/test_prompt_version.py` 会失败并告诉你要登记
    （而不是在数据里悄悄留下无法解释的行）。
    """
    return PROMPT_VERSIONS.get(name)


__all__ = [
    "COMBINED_ANALYSIS_SYSTEM_PROMPT",
    "PROMPT_VERSIONS",
    "QUESTION_SYSTEM_PROMPT",
    "UNDERSTANDING_SYSTEM_PROMPT",
    "generate_extension_knowledge_messages",
    "generate_questions_batch_messages",
    "generate_questions_messages",
    "grade_short_answer_messages",
    "infer_card_relations_messages",
    "prompt_version",
    "rag_answer_messages",
    "summarize_chapter_messages",
]
