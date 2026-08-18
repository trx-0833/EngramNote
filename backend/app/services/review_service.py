"""
复习调度服务模块

本模块提供复习调度核心业务逻辑，包括到期题目查询、答案提交判分、
SM-2 参数更新和复习统计等功能。

主要职责：
- 查询今日到期复习题目
- 处理用户答案提交（判分 + SM-2 更新 + 记录日志）
- 计算复习统计数据

设计决策：
- 到期题目按 next_review_at 升序排列（最过期的优先）
- 新题目（next_review_at 为 None）视为立即可复习
- 答题后即时更新 SM-2 参数，无需异步任务
- 复习统计按 UTC 日期计算
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, func, and_, case, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models.note import Note
from ..models.quiz_item import QuizItem, QuestionType
from ..models.review_log import ReviewLog
from ..models.knowledge_card import KnowledgeCard
from ..services.sm2_service import calculate_sm2, quality_from_answer

logger = logging.getLogger(__name__)
settings = get_settings()


DAILY_REVIEW_LIMIT = settings.daily_review_limit  # 每日最大答题数（F-12：单一来源 config）


async def get_due_quizzes(
    user_id: str,
    db: AsyncSession,
    limit: int = 50,
    daily_max: int = DAILY_REVIEW_LIMIT,
) -> List[QuizItem]:
    """
    获取用户今日到期的复习题目

    查询条件：
    - next_review_at <= 当前时间（已到期）
    - next_review_at 为 None（新题目，尚未设置复习时间）
    - 每日最多返回 daily_max 道题（按今日已答题数扣减）

    排序策略：
    1. 最过期的优先（next_review_at 升序，nullsfirst）
    2. 薄弱点优先：关联卡片的错误次数越多，排序越靠前

    Args:
        user_id: 用户 ID
        db: 数据库会话
        limit: 最大返回数量
        daily_max: 每日最大答题数

    Returns:
        List[QuizItem]: 到期题目列表
    """
    now = datetime.now(timezone.utc)
    # F-32 修复：日界按 Asia/Shanghai（北京时间零点），而非 UTC 零点
    from ..utils.timeutil import today_start_utc
    today_start = today_start_utc(now)

    # 计算今日已答题数
    today_done_result = await db.execute(
        select(func.count()).select_from(ReviewLog).where(
            ReviewLog.user_id == user_id,
            ReviewLog.review_at >= today_start,
        )
    )
    today_done = today_done_result.scalar() or 0

    # 今日已达到上限，返回空列表
    remaining = daily_max - today_done
    if remaining <= 0:
        return []

    # 实际返回数量 = min(剩余配额, limit)
    actual_limit = min(remaining, limit)

    # 子查询：统计每张卡片的错误次数，用于薄弱点优先排序
    # （与主查询一致地排除回收站笔记的题目，保证排序口径一致）
    error_subq = (
        select(
            QuizItem.card_id,
            func.coalesce(func.sum(case((ReviewLog.is_correct == False, 1), else_=0)), 0).label("error_count"),
        )
        .join(ReviewLog, ReviewLog.quiz_id == QuizItem.id, isouter=True)
        .where(
            QuizItem.user_id == user_id,
            or_(
                QuizItem.note_id.is_(None),
                select(Note.id).where(
                    Note.id == QuizItem.note_id, Note.trashed_at.is_(None)
                ).exists(),
            ),
        )
        .group_by(QuizItem.card_id)
        .subquery()
    )

    # 查询到期题目，关联薄弱点排序（回收站笔记的题目暂不可见）
    result = await db.execute(
        select(QuizItem).where(
            QuizItem.user_id == user_id,
            (QuizItem.next_review_at <= now) | (QuizItem.next_review_at.is_(None)),
            or_(
                QuizItem.note_id.is_(None),
                select(Note.id).where(
                    Note.id == QuizItem.note_id, Note.trashed_at.is_(None)
                ).exists(),
            ),
        )
        .outerjoin(error_subq, error_subq.c.card_id == QuizItem.card_id)
        .order_by(
            QuizItem.next_review_at.asc().nullsfirst(),
            func.coalesce(error_subq.c.error_count, 0).desc(),
        )
        .limit(actual_limit)
    )
    return list(result.scalars().all())


async def submit_answer(
    quiz_id: str,
    user_id: str,
    user_answer: str,
    time_spent_ms: int,
    db: AsyncSession,
    skip_daily_limit: bool = False,
    skip_due_check: bool = False,
) -> Dict[str, Any]:
    """
    提交答案并更新 SM-2 调度参数

    完整流程：
    1. 查询题目，校验权限
    2. 校验题目是否到期（普通复习；快速复习可跳过）
    3. 同日重复提交幂等：已提交过则返回已记录结果，不重复写日志/叠加 SM-2
    4. 判断正误，计算 SM-2 评分
    5. 调用 SM-2 算法更新调度参数
    6. 更新 QuizItem 的 SM-2 字段
    7. 创建 ReviewLog 记录
    8. 返回判分结果

    Args:
        quiz_id: 题目 ID
        user_id: 用户 ID
        user_answer: 用户答案
        time_spent_ms: 答题耗时（毫秒）
        db: 数据库会话
        skip_daily_limit: 是否跳过每日答题限额检查（快速复习场景使用）
        skip_due_check: 是否跳过到期校验（F-14 修复：快速复习保留免校验，
                        普通复习必须到期才能提交）

    Returns:
        Dict: 判分结果，包含 is_correct, quality, correct_answer, explanation, next_review_at 等
    """
    # 1. 查询题目
    result = await db.execute(
        select(QuizItem).where(
            QuizItem.id == quiz_id,
            QuizItem.user_id == user_id,
        )
    )
    quiz = result.scalars().first()
    if not quiz:
        return {"error": "题目不存在"}

    # 1.5 检查每日答题限额（快速复习场景跳过此检查）
    now = datetime.now(timezone.utc)
    if not skip_daily_limit:
        # F-32 修复：日界按 Asia/Shanghai（北京时间零点）
        from ..utils.timeutil import today_start_utc
        today_start = today_start_utc(now)
        today_done_result = await db.execute(
            select(func.count()).select_from(ReviewLog).where(
                ReviewLog.user_id == user_id,
                ReviewLog.review_at >= today_start,
            )
        )
        today_done = today_done_result.scalar() or 0
        if today_done >= DAILY_REVIEW_LIMIT:
            return {"error": f"今日已完成 {today_done} 道题，已达每日上限 {DAILY_REVIEW_LIMIT}"}

    # 1.6 F-14 修复：同日同题幂等——今日已提交过则直接返回已记录结果，
    #     不重复创建 ReviewLog、不重复叠加 SM-2（防止双击/连点/API 重放）。
    #     幂等检查必须在到期校验之前：同日已提交后 SM-2 已把 next_review_at
    #     推到未来，若先查到期会误报"未到期"而非命中幂等。
    from ..utils.timeutil import today_start_utc
    today_start = today_start_utc(now)
    existing_log_result = await db.execute(
        select(ReviewLog).where(
            ReviewLog.quiz_id == quiz_id,
            ReviewLog.user_id == user_id,
            ReviewLog.review_at >= today_start,
        ).order_by(ReviewLog.review_at.desc()).limit(1)
    )
    existing_log = existing_log_result.scalars().first()
    if existing_log is not None:
        logger.info(
            f"答题提交幂等命中: user={user_id[:8]}, quiz={quiz_id[:8]}, "
            f"同日已提交，返回历史结果"
        )
        return _build_submit_result(quiz, existing_log)

    # 1.7 F-14 修复：普通复习提交校验题目是否到期（未到期拒绝；快速复习跳过）
    if not skip_due_check and quiz.next_review_at is not None and quiz.next_review_at > now:
        return {"error": "题目尚未到期，请按复习计划进行"}

    # 2. 判断正误，计算 SM-2 评分
    correct_answer = quiz.answer
    question_type = quiz.question_type.value

    quality = quality_from_answer(question_type, user_answer, correct_answer)
    is_correct = quality >= 3

    # 3. 调用 SM-2 算法
    sm2_result = calculate_sm2(
        quality=quality,
        interval=quiz.interval,
        repetition=quiz.repetition,
        easiness_factor=quiz.easiness_factor,
    )

    # 4. 更新 QuizItem 的 SM-2 字段
    quiz.interval = sm2_result.interval
    quiz.repetition = sm2_result.repetition
    quiz.easiness_factor = sm2_result.easiness_factor
    quiz.next_review_at = sm2_result.next_review_at
    quiz.last_reviewed_at = now
    quiz.review_count += 1

    # 5. 创建 ReviewLog 记录
    review_log = ReviewLog(
        user_id=user_id,
        quiz_id=quiz_id,
        note_id=quiz.note_id,
        user_answer=user_answer,
        is_correct=is_correct,
        quality=quality,
        time_spent_ms=time_spent_ms,
        review_at=now,
    )
    db.add(review_log)

    await db.commit()
    await db.refresh(quiz)

    logger.info(
        f"答题提交: user={user_id[:8]}, quiz={quiz_id[:8]}, "
        f"correct={is_correct}, quality={quality}, interval={sm2_result.interval}"
    )

    # 答题后非阻塞刷新关联卡片掌握度（失败不影响答题响应）
    try:
        from .mastery_service import refresh_card_mastery
        await refresh_card_mastery(quiz.card_id, db)
    except Exception as mastery_err:
        logger.warning(
            f"刷新卡片掌握度失败 (card_id={quiz.card_id}): {mastery_err}"
        )

    return _build_submit_result(quiz, review_log)


def _build_submit_result(quiz, review_log) -> Dict[str, Any]:
    """构造提交结果字典（普通提交与幂等命中共用）"""
    # 解析选项（选择题）
    options = None
    if quiz.options:
        try:
            options = json.loads(quiz.options) if isinstance(quiz.options, str) else quiz.options
        except (json.JSONDecodeError, TypeError):
            options = None
    return {
        "quiz_id": quiz.id,
        "is_correct": review_log.is_correct,
        "quality": review_log.quality,
        "correct_answer": quiz.answer,
        "explanation": quiz.explanation,
        "options": options,
        "question_type": quiz.question_type.value,
        "sm2": {
            "interval": quiz.interval,
            "repetition": quiz.repetition,
            "easiness_factor": quiz.easiness_factor,
            "next_review_at": quiz.next_review_at.isoformat() if quiz.next_review_at else None,
        },
    }


async def get_review_stats(
    user_id: str,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    获取用户复习统计数据

    统计维度：
    - 今日待复习数
    - 今日已完成数
    - 今日正确率
    - 累计复习次数
    - 累计正确率

    Args:
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        Dict: 复习统计数据
    """
    now = datetime.now(timezone.utc)
    # F-32 修复：日界按 Asia/Shanghai（北京时间零点），而非 UTC 零点
    from ..utils.timeutil import today_start_utc
    today_start = today_start_utc(now)

    # 今日已完成数（先查，后面 due_count 需要用）
    today_done_result = await db.execute(
        select(func.count()).select_from(ReviewLog).where(
            ReviewLog.user_id == user_id,
            ReviewLog.review_at >= today_start,
        )
    )
    today_done = today_done_result.scalar() or 0

    # 今日待复习数（next_review_at <= now 或为 None），考虑每日限额
    remaining_quota = max(0, DAILY_REVIEW_LIMIT - today_done)
    if remaining_quota <= 0:
        due_count = 0
    else:
        # 使用子查询加 LIMIT，避免扫描全表（回收站笔记的题目暂不可见）
        due_subq = (
            select(func.count())
            .select_from(
                select(QuizItem.id)
                .where(
                    QuizItem.user_id == user_id,
                    (QuizItem.next_review_at <= now) | (QuizItem.next_review_at.is_(None)),
                    or_(
                        QuizItem.note_id.is_(None),
                        select(Note.id).where(
                            Note.id == QuizItem.note_id, Note.trashed_at.is_(None)
                        ).exists(),
                    ),
                )
                .limit(remaining_quota)
                .subquery()
            )
        )
        due_count_result = await db.execute(due_subq)
        due_count = due_count_result.scalar() or 0

    # 今日正确数
    today_correct_result = await db.execute(
        select(func.count()).select_from(ReviewLog).where(
            ReviewLog.user_id == user_id,
            ReviewLog.review_at >= today_start,
            ReviewLog.is_correct == True,
        )
    )
    today_correct = today_correct_result.scalar() or 0

    # 累计复习次数
    total_reviews_result = await db.execute(
        select(func.count()).select_from(ReviewLog).where(
            ReviewLog.user_id == user_id,
        )
    )
    total_reviews = total_reviews_result.scalar() or 0

    # 累计正确数
    total_correct_result = await db.execute(
        select(func.count()).select_from(ReviewLog).where(
            ReviewLog.user_id == user_id,
            ReviewLog.is_correct == True,
        )
    )
    total_correct = total_correct_result.scalar() or 0

    # 总题目数（回收站笔记的题目暂不可见，不计入）
    total_quizzes_result = await db.execute(
        select(func.count()).select_from(QuizItem).where(
            QuizItem.user_id == user_id,
            or_(
                QuizItem.note_id.is_(None),
                select(Note.id).where(
                    Note.id == QuizItem.note_id, Note.trashed_at.is_(None)
                ).exists(),
            ),
        )
    )
    total_quizzes = total_quizzes_result.scalar() or 0

    return {
        "due_count": due_count,
        "today_done": today_done,
        "today_correct": today_correct,
        "today_accuracy": round(today_correct / today_done * 100, 1) if today_done > 0 else 0,
        "total_reviews": total_reviews,
        "total_correct": total_correct,
        "total_accuracy": round(total_correct / total_reviews * 100, 1) if total_reviews > 0 else 0,
        "total_quizzes": total_quizzes,
        "daily_limit": DAILY_REVIEW_LIMIT,  # F-12：每日答题上限下发给前端
    }


async def get_review_history(
    user_id: str,
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    """
    获取用户复习历史（分页）

    Args:
        user_id: 用户 ID
        db: 数据库会话
        page: 页码
        page_size: 每页数量

    Returns:
        Dict: 复习历史列表和分页信息
    """
    # 计算总数
    count_result = await db.execute(
        select(func.count()).select_from(ReviewLog).where(
            ReviewLog.user_id == user_id,
        )
    )
    total = count_result.scalar() or 0

    # 分页查询
    result = await db.execute(
        select(ReviewLog).where(
            ReviewLog.user_id == user_id,
        ).order_by(
            ReviewLog.review_at.desc()
        ).offset((page - 1) * page_size).limit(page_size)
    )
    logs = list(result.scalars().all())

    items = []
    for log in logs:
        items.append({
            "id": log.id,
            "quiz_id": log.quiz_id,
            "note_id": log.note_id,
            "user_answer": log.user_answer,
            "is_correct": log.is_correct,
            "quality": log.quality,
            "time_spent_ms": log.time_spent_ms,
            "review_at": log.review_at.isoformat() if log.review_at else None,
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
