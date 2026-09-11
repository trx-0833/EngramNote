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

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

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


#: 语义判分的超时上限（秒）
#:
#: ## 为什么必须给判分单独设一个远小于全局的超时
#:
#: 全局 `llm_timeout_seconds=600`、`llm_max_retries=5`（每次 1s 退避）。
#: 语义判分是**在用户提交复习答案的同步路径上**调用的 —— 若沿用全局设置，
#: 最坏情况用户要等在原地十几分钟，而复习是高频操作。
#:
#: 实测证据：接入语义判分后测试套件从 70 秒涨到 **183 秒** ——
#: `test_self_rating.py` 每个用例 10~14 秒，因为它们正是
#: 「简答题 + 未自评」这个组合，每次都在等 LLM 重试耗尽。
#: 测试里网络是被守卫阻断的（立即抛错），生产里则可能真的等下去。
#:
#: 超时后返回 None → 调用方退回自评占位。**这个降级路径本来就是两阶段
#: 流程的常态**（用户自评才是主评分来源），所以超时不会让任何一次复习
#: 失败或卡住 —— 只是少了一次自动判分。
SEMANTIC_GRADE_TIMEOUT_SECONDS = 20.0

#: 语义判分可被采信的最低置信度
#:
#: 低于它时**退回用户自评占位**，而不是勉强采用。理由见
#: `grade_short_answer_semantically` 的说明：LLM 判分也有把握不准的时候，
#: 而偏差一旦进入调度（改变了 interval）就**无法事后纠正**。
SEMANTIC_CONFIDENCE_THRESHOLD = 0.7

#: verdict → SM-2 quality 的映射（这是**策略**，会随阈值调整而变）
#:
#: ## 为什么 partial 映射到 3 而不是 2
#:
#: SM-2 里 `quality >= 3` 才算"答对"（`is_correct` 用的就是这个门槛）。
#: 把"说对一半"判成 2 会让它算作答错，从而**重置间隔** ——
#: 用户明明记住了一半，却被当作完全没记住重新开始。
#: 这比"把半分当及格"更伤：过早重置会让长期复习永远推进不下去。
#:
#: ## 为什么 correct 有 4 和 5 两档
#:
#: 高置信度的完全正确给 5（间隔增长最快）；置信度一般但判定为正确给 4
#: （增长稍慢）。这样"模型很确定"与"模型判断对但把握一般"不会得到
#: 相同的调度后果 —— 后者增长慢一点，是廉价的纠错余量。
VERDICT_TO_QUALITY = {
    "correct": 4,
    "partial": 3,
    "incorrect": 1,
}
#: 高置信度时 correct 提升到 5 的阈值
HIGH_CONFIDENCE = 0.85


async def grade_short_answer_semantically(
    question: str,
    expected_answer: str,
    user_answer: str,
    *,
    user_id: Optional[str] = None,
    note_id: Optional[str] = None,
) -> Optional[dict]:
    """用 LLM 做简答题语义判分，返回与 ``grade_answer`` 同构的 dict

    ## 与 ``grade_answer`` 的关系

    ``grade_answer`` 是**纯函数**（无 IO、无异步），对简答题一律返回
    ``ungraded`` 占位。本函数是它的**异步补充**：只在调用方明确需要
    语义判分时才调用，把结果转换成同一套 ``quality`` 口径。

    拆成两个函数而不是把 ``grade_answer`` 改成 async：
    前者被多处同步调用（`quality_from_answer` 等），改成 async 会波及
    整个调用链，而其中多数场景并不需要 LLM。

    ## 返回值

    成功且置信度达标时返回 ``grade_answer`` 同构 dict（``method="semantic"``）；
    任何一步失败、或置信度不足时返回 **None** —— 调用方据此退回自评占位。

    **None 不等于答错**：它是"未判分"。把它当答错会让整个简答题池的间隔
    被误重置。
    """
    if not (user_answer or "").strip():
        # 未作答不需要调用 LLM，语义上确定是错
        return {
            "quality": 0,
            "method": "semantic",
            "needs_self_assessment": False,
            "reason": "未作答",
            "detail": {"verdict": "incorrect", "confidence": 1.0},
        }

    from .llm_service import LLMService
    from .llm_accounting_service import llm_context

    try:
        # 超时上限**远小于**全局 llm_timeout_seconds：判分在复习提交的同步
        # 路径上，用户就等在原地（见 SEMANTIC_GRADE_TIMEOUT_SECONDS 的说明）。
        #
        # 阶段 4.2：带上上下文，让这次判分的花费能归到具体用户/笔记。
        with llm_context(user_id=user_id, note_id=note_id, task="grade_short_answer"):
            detail = await asyncio.wait_for(
                LLMService().grade_short_answer(
                    question=question,
                    expected_answer=expected_answer,
                    user_answer=user_answer,
                ),
                timeout=SEMANTIC_GRADE_TIMEOUT_SECONDS,
            )
    except asyncio.TimeoutError:
        logger.info(
            "语义判分超时（>%.0fs），退回自评占位", SEMANTIC_GRADE_TIMEOUT_SECONDS
        )
        return None
    except Exception as exc:  # 判分服务异常不该让复习提交失败
        logger.warning("语义判分异常，退回自评占位: %s", exc)
        return None

    if not detail:
        return None

    confidence = float(detail.get("confidence") or 0.0)
    if confidence < SEMANTIC_CONFIDENCE_THRESHOLD:
        logger.info(
            "语义判分置信度不足（%.2f < %.2f），退回自评占位: verdict=%s",
            confidence, SEMANTIC_CONFIDENCE_THRESHOLD, detail.get("verdict"),
        )
        return None

    verdict = detail["verdict"]
    quality = VERDICT_TO_QUALITY[verdict]
    if verdict == "correct" and confidence >= HIGH_CONFIDENCE:
        quality = 5

    missing = detail.get("missing_points") or []
    miscon = detail.get("misconceptions") or []
    reason = detail.get("reason") or ""
    # 给用户看的理由要带上具体缺失点 —— 只说"部分正确"没有指导价值
    if verdict == "partial" and missing:
        reason = f"{reason}（遗漏：{'；'.join(missing[:3])}）" if reason else (
            f"遗漏：{'；'.join(missing[:3])}"
        )
    elif verdict == "incorrect" and miscon:
        reason = f"{reason}（误解：{'；'.join(miscon[:3])}）" if reason else (
            f"误解：{'；'.join(miscon[:3])}"
        )

    return {
        "quality": quality,
        "method": "semantic",
        "needs_self_assessment": False,
        "reason": reason or "语义判分",
        "detail": detail,
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
