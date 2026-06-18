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


def quality_from_answer(
    question_type: str,
    user_answer: str,
    correct_answer: str,
) -> int:
    """
    根据题目类型和用户答案计算 SM-2 评分

    评分策略：
    - choice（选择题）：完全匹配 → 5，否则 → 1
    - fill_blank（填空题）：完全匹配 → 5，部分匹配 → 3，不匹配 → 1
    - short_answer（简答题）：基于关键词匹配评分 → 1-5

    Args:
        question_type: 题目类型（choice/fill_blank/short_answer）
        user_answer: 用户提交的答案
        correct_answer: 正确答案

    Returns:
        int: SM-2 评分 (0-5)
    """
    user_lower = user_answer.strip().lower()
    correct_lower = correct_answer.strip().lower()

    if not user_lower:
        return 0  # 空答案

    if question_type == "choice":
        # 选择题：支持多种答案格式匹配
        # 数据库 answer 可能是 "A"、"A. 选项文本" 或纯文本
        # 用户提交的可能是完整选项文本 "A. 选项文本"
        if user_lower == correct_lower:
            return 5
        # 提取选项字母前缀（A/B/C/D）进行匹配
        user_letter = re.match(r'^([a-d])', user_lower)
        correct_letter = re.match(r'^([a-d])', correct_lower)
        if user_letter and correct_letter and user_letter.group(1) == correct_letter.group(1):
            return 5
        # 用户提交了完整选项文本，answer 只是字母
        if correct_letter and not user_letter:
            # answer="a", user="a. 选项文本" → 检查用户答案是否以该字母开头
            if user_lower.startswith(correct_letter.group(1)):
                return 5
        # 用户提交了字母，answer 是完整选项文本
        if user_letter and not correct_letter:
            if correct_lower.startswith(user_letter.group(1)):
                return 5
        # 去掉字母前缀后比较纯文本内容
        user_text = re.sub(r'^[a-d][.、\s]\s*', '', user_lower).strip()
        correct_text = re.sub(r'^[a-d][.、\s]\s*', '', correct_lower).strip()
        if user_text and correct_text and user_text == correct_text:
            return 5
        return 1

    elif question_type == "fill_blank":
        # 填空题：完全匹配或部分匹配
        if user_lower == correct_lower:
            return 5
        # 检查部分匹配（答案包含在用户回答中，或反过来）
        if correct_lower in user_lower or user_lower in correct_lower:
            return 3
        # 检查关键词重叠
        user_words = set(user_lower)
        correct_words = set(correct_lower)
        overlap = len(user_words & correct_words)
        total = len(correct_words) if correct_words else 1
        if overlap / total > 0.5:
            return 3
        return 1

    else:
        # short_answer（简答题）：基于关键词匹配评分
        return _score_short_answer(user_lower, correct_lower)


def _score_short_answer(user_answer: str, correct_answer: str) -> int:
    """
    简答题评分：基于关键词匹配

    使用 n-gram 关键词匹配策略（与 RAG 服务一致），
    计算用户答案与正确答案的重叠度，映射到 1-5 评分。

    Args:
        user_answer: 用户答案（已转小写）
        correct_answer: 正确答案（已转小写）

    Returns:
        int: SM-2 评分 (1-5)
    """
    # 提取关键词（2-4字的中文词组 + 英文单词）
    def extract_keywords(text: str) -> set:
        keywords = set()
        # 中文 n-gram (2-4字)
        for length in range(4, 1, -1):
            for i in range(len(text) - length + 1):
                segment = text[i:i + length]
                # 只保留包含中文的片段
                if any('\u4e00' <= c <= '\u9fff' for c in segment):
                    keywords.add(segment)
        # 英文单词
        import re
        english_words = re.findall(r'[a-zA-Z]+', text)
        for word in english_words:
            if len(word) >= 2:
                keywords.add(word.lower())
        return keywords

    user_keywords = extract_keywords(user_answer)
    correct_keywords = extract_keywords(correct_answer)

    if not correct_keywords:
        # 没有可匹配的关键词，做简单的字符串包含检查
        if user_answer and correct_answer in user_answer:
            return 4
        return 2

    # 计算关键词覆盖率
    matched = len(user_keywords & correct_keywords)
    total = len(correct_keywords)
    coverage = matched / total

    # 映射到 1-5 评分
    if coverage >= 0.8:
        return 5
    elif coverage >= 0.6:
        return 4
    elif coverage >= 0.4:
        return 3
    elif coverage >= 0.2:
        return 2
    else:
        return 1
