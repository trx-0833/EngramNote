"""
SM-2 间隔重复算法模块

本模块实现了标准 SM-2（SuperMemo 2）间隔重复算法，
用于根据用户答题表现动态调整复习间隔。

SM-2 算法核心：
- 根据用户对题目的回忆质量（quality, 0-5）调整复习间隔
- 舒适度因子（easiness_factor）反映题目对用户的难易程度
- 连续正确回忆（repetition）决定间隔增长速度
- 回忆失败时重置间隔，重新开始记忆周期

评分等级说明：
- 5: 完美记忆，毫不费力
- 4: 正确但有些犹豫
- 3: 勉强正确，费了很大力气
- 2: 错误，但看到答案后觉得熟悉
- 1: 错误，答案看起来有些印象
- 0: 完全忘记，毫无印象

设计决策：
- 遵循 Karpathy 风格：from-scratch, 无外部依赖
- EF 最小值 1.3，防止间隔过短
- 首次复习间隔为1天，第二次6天，之后按 EF 递增
"""

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


@dataclass
class SM2Result:
    """
    SM-2 算法计算结果

    Attributes:
        interval: 新的复习间隔（天）
        repetition: 新的连续正确次数
        easiness_factor: 新的舒适度因子
        next_review_at: 下次复习时间
    """
    interval: int
    repetition: int
    easiness_factor: float
    next_review_at: datetime


def calculate_sm2(
    quality: int,
    interval: int = 1,
    repetition: int = 0,
    easiness_factor: float = 2.5,
) -> SM2Result:
    """
    SM-2 算法核心计算

    根据用户回忆质量更新间隔重复参数。

    算法逻辑：
    1. 更新舒适度因子：EF' = EF + (0.1 - (5-q)*(0.08+(5-q)*0.02))
    2. quality >= 3（回忆成功）：
       - repetition += 1
       - 间隔递增：首次1天，第二次6天，之后 interval * EF
    3. quality < 3（回忆失败）：
       - repetition = 0
       - interval = 1（重置为1天）

    Args:
        quality: 回忆质量评分 (0-5)
        interval: 当前复习间隔（天）
        repetition: 当前连续正确次数
        easiness_factor: 当前舒适度因子

    Returns:
        SM2Result: 包含更新后的参数和下次复习时间
    """
    # 参数校验
    quality = max(0, min(5, quality))
    easiness_factor = max(1.3, easiness_factor)

    # 1. 更新舒适度因子
    new_ef = easiness_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    new_ef = max(1.3, new_ef)  # EF 最小值 1.3

    # 2. 根据 quality 更新 repetition 和 interval
    if quality >= 3:
        # 回忆成功
        new_repetition = repetition + 1
        if new_repetition == 1:
            new_interval = 1
        elif new_repetition == 2:
            new_interval = 6
        else:
            new_interval = round(interval * new_ef)
    else:
        # 回忆失败，重置
        new_repetition = 0
        new_interval = 1

    # 3. 计算下次复习时间
    next_review_at = datetime.now(timezone.utc) + timedelta(days=new_interval)

    return SM2Result(
        interval=new_interval,
        repetition=new_repetition,
        easiness_factor=round(new_ef, 2),
        next_review_at=next_review_at,
    )



"""Auto-grading for review answers.

Design notes (see docs/overhaul-plan.md 2.4 L-1):

The previous implementation graded free-text answers by set-overlap of character
n-grams. Measured behaviour:

  * a logically INVERTED answer scored quality=4 ("correct, with hesitation")
  * a correct but terse answer ("机器学习" against a 62-char reference) scored
    quality=1 (failed), because coverage is divided by the reference length
  * fill-in-the-blank compared SINGLE-CHARACTER sets, so "学器" was accepted
    as "机器学习"

Root causes: n-gram sets are unordered (negation is invisible) and
length-asymmetric. Character-level overlap cannot represent meaning.

New policy:
  * choice            -> deterministic letter/option matching
  * fill_blank        -> normalized exact match, then bounded edit distance
  * short_answer      -> NOT auto-graded. Returns quality=1 with
                         needs_self_assessment=True so the caller/UI can ask
                         the learner (self-rating is the most reliable signal,
                         and is what the overhaul plan prescribes as layer 1).

Callers must treat needs_self_assessment as "do not trust this grade".
"""

# 注意：re / unicodedata 已在文件顶部导入（见 sm2_service 的 import 段），此处不重复导入

# --- Normalization -----------------------------------------------------------

_PUNCT_RE = re.compile(r"[\s，。、；：！？""''（）【】《》,.;:!?\"'()\[\]{}<>~`|/\\_\-—…]+")
_LATIN_WS_RE = re.compile(r"\s+")


def normalize_answer(text: str) -> str:
    """Normalize an answer for comparison.

    Folds full-width to half-width, lowercases, strips all whitespace and
    punctuation. Chinese is unaffected apart from punctuation.
    """
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", text)
    s = s.lower()
    s = _PUNCT_RE.sub("", s)
    return _LATIN_WS_RE.sub("", s)


def _levenshtein(a: str, b: str) -> int:
    """Levenshtein edit distance (iterative, O(min(|a|,|b|)) space)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(
                previous[j] + 1,        # deletion
                current[j - 1] + 1,     # insertion
                previous[j - 1] + (ca != cb),  # substitution
            ))
        previous = current
    return previous[-1]


def _levenshtein_ratio(a: str, b: str) -> float:
    """Edit distance normalized to [0, 1]; 1.0 == identical."""
    longest = max(len(a), len(b))
    if longest == 0:
        return 1.0
    return 1.0 - _levenshtein(a, b) / longest


# --- Grading -----------------------------------------------------------------

# Option letters accepted for choice questions.
_CHOICE_LETTER_RE = re.compile(r"^([a-d])\b")


def _grade_choice(user_answer: str, correct_answer: str) -> int:
    """Deterministic grading for choice questions.

    Accepts the answer as a bare letter, a letter with the option text, or the
    full option text. Ambiguous submissions are treated as wrong rather than
    guessed at.
    """
    u = normalize_answer(user_answer)
    c = normalize_answer(correct_answer)
    if not u:
        return 0
    if u == c:
        return 5
    # Compare leading option letters when both start with one.
    ul = _CHOICE_LETTER_RE.match(u)
    cl = _CHOICE_LETTER_RE.match(c)
    if ul and cl:
        return 5 if ul.group(1) == cl.group(1) else 1
    # One side is a letter, the other starts with that letter followed by text
    # (normalization may have removed the separating punctuation).
    if cl and not ul and u.startswith(cl.group(1)):
        return 5
    if ul and not cl and c.startswith(ul.group(1)):
        return 5
    return 1


def _grade_fill_blank(user_answer: str, correct_answer: str) -> int:
    """Grading for fill-in-the-blank: exact match after normalization,
    then small typo tolerance via edit distance.

    Explicitly does NOT use character-set overlap: "学器" is not "机器学习".
    """
    u = normalize_answer(user_answer)
    c = normalize_answer(correct_answer)
    if not u:
        return 0
    if u == c:
        return 5
    if not c:
        return 1
    ratio = _levenshtein_ratio(u, c)
    # Allow one typo in a short answer, scaling to ~15% for longer ones.
    typo_budget = max(1, int(len(c) * 0.15))
    distance = _levenshtein(u, c)
    if distance <= typo_budget and ratio >= 0.7:
        return 3
    # A strict substring match only counts when it covers most of the answer,
    # so that "学习" does not pass for "机器学习".
    if len(u) >= 3 and (c in u or u in c):
        shorter, longer = sorted((len(u), len(c)))
        if shorter / longer >= 0.8:
            return 3
    return 1


def grade_answer(
    question_type: str,
    user_answer: str,
    correct_answer: str,
) -> dict:
    """Grade an answer and report how much the grade can be trusted.

    Returns:
        dict with keys:
            quality (int): SM-2 quality 0-5
            method (str): "exact" | "choice" | "edit_distance" | "ungraded"
            needs_self_assessment (bool): True when the automatic grade is a
                placeholder and the learner should rate themselves instead.
            reason (str): human-readable explanation (Chinese) shown in the UI.
    """
    qtype = (question_type or "").strip().lower()

    if not (user_answer or "").strip():
        return {
            "quality": 0,
            "method": "exact",
            "needs_self_assessment": False,
            "reason": "未作答",
        }

    if qtype == "choice":
        q = _grade_choice(user_answer, correct_answer)
        return {
            "quality": q,
            "method": "choice",
            "needs_self_assessment": False,
            "reason": "选择题按选项字母判定",
        }

    if qtype == "fill_blank":
        q = _grade_fill_blank(user_answer, correct_answer)
        return {
            "quality": q,
            "method": "exact" if q == 5 else "edit_distance",
            "needs_self_assessment": False,
            "reason": "填空题按归一化后的精确匹配/编辑距离判定",
        }

    # short_answer (and anything unknown): do not pretend to grade meaning.
    return {
        "quality": 1,
        "method": "ungraded",
        "needs_self_assessment": True,
        "reason": (
            "简答题无法用字符匹配可靠判分（实测：把正确答案的逻辑完全反转，"
            "旧算法仍判为「正确」）。请自行评估掌握程度。"
        ),
    }


def quality_from_answer(
    question_type: str,
    user_answer: str,
    correct_answer: str,
) -> int:
    """Backward-compatible wrapper returning only the SM-2 quality.

    Prefer grade_answer() when the caller can act on needs_self_assessment.
    """
    return grade_answer(question_type, user_answer, correct_answer)["quality"]
