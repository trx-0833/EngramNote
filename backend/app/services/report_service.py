"""
学习报告服务模块

本模块提供学习报告核心业务逻辑，包括今日学习报告聚合、
7天趋势统计和薄弱点分析等功能。

主要职责：
- 聚合今日学习数据（新掌握数、复习时长、各题型正确率）
- 计算最近7天的复习趋势
- 分析薄弱点（错误次数最多的知识点卡片）

设计决策：
- 所有统计按 UTC 日期计算，与复习调度保持一致
- 薄弱点定义为：关联题目错误次数最多的知识卡片
- 新掌握知识点 = 今日首次答对（quality >= 3）的题目数
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from sqlalchemy import select, func, and_, case, distinct
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.quiz_item import QuizItem, QuestionType
from ..models.review_log import ReviewLog
from ..models.knowledge_card import KnowledgeCard
from ..models.note import Note

logger = logging.getLogger(__name__)


async def get_daily_report(
    user_id: str,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    获取今日学习报告

    聚合维度：
    - 今日新掌握知识点数（quality >= 3 且首次答对的 QuizItem）
    - 今日复习总时长
    - 今日复习总数和正确率
    - 各题型正确率

    Args:
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        Dict: 今日学习报告数据
    """
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_str = today_start.strftime("%Y-%m-%d")

    # 今日复习总数
    today_total_result = await db.execute(
        select(func.count()).select_from(ReviewLog).where(
            ReviewLog.user_id == user_id,
            ReviewLog.review_at >= today_start,
        )
    )
    today_total = today_total_result.scalar() or 0

    # 今日正确数
    today_correct_result = await db.execute(
        select(func.count()).select_from(ReviewLog).where(
            ReviewLog.user_id == user_id,
            ReviewLog.review_at >= today_start,
            ReviewLog.is_correct == True,
        )
    )
    today_correct = today_correct_result.scalar() or 0

    # 今日复习总时长
    today_time_result = await db.execute(
        select(func.coalesce(func.sum(ReviewLog.time_spent_ms), 0)).where(
            ReviewLog.user_id == user_id,
            ReviewLog.review_at >= today_start,
        )
    )
    today_time = today_time_result.scalar() or 0

    # 今日新掌握知识点数：今日首次答对（quality >= 3）的 QuizItem 数量
    # 即：今日答对的题目中，review_count 为 1 的（说明是首次答对）
    new_mastered_result = await db.execute(
        select(func.count(distinct(ReviewLog.quiz_id))).select_from(ReviewLog).join(
            QuizItem, QuizItem.id == ReviewLog.quiz_id
        ).where(
            ReviewLog.user_id == user_id,
            ReviewLog.review_at >= today_start,
            ReviewLog.is_correct == True,
            QuizItem.review_count == 1,
        )
    )
    new_mastered = new_mastered_result.scalar() or 0

    # 各题型正确率
    type_accuracy = []
    for qt in QuestionType:
        type_total_result = await db.execute(
            select(func.count()).select_from(ReviewLog).join(
                QuizItem, QuizItem.id == ReviewLog.quiz_id
            ).where(
                ReviewLog.user_id == user_id,
                ReviewLog.review_at >= today_start,
                QuizItem.question_type == qt,
            )
        )
        type_total = type_total_result.scalar() or 0

        type_correct_result = await db.execute(
            select(func.count()).select_from(ReviewLog).join(
                QuizItem, QuizItem.id == ReviewLog.quiz_id
            ).where(
                ReviewLog.user_id == user_id,
                ReviewLog.review_at >= today_start,
                QuizItem.question_type == qt,
                ReviewLog.is_correct == True,
            )
        )
        type_correct = type_correct_result.scalar() or 0

        type_accuracy.append({
            "question_type": qt.value,
            "total": type_total,
            "correct": type_correct,
            "accuracy": round(type_correct / type_total * 100, 1) if type_total > 0 else 0,
        })

    # 薄弱点数量
    weak_points = await get_weak_points(user_id, db, limit=5)

    result = {
        "date": today_str,
        "new_mastered": new_mastered,
        "total_review_time_ms": today_time,
        "total_reviews": today_total,
        "today_accuracy": round(today_correct / today_total * 100, 1) if today_total > 0 else 0,
        "weak_point_count": len(weak_points.get("items", [])),
        "question_type_accuracy": type_accuracy,
    }

    logger.info(f"学习报告生成: user={user_id[:8]}, 新掌握={new_mastered}, 复习={today_total}, 正确率={result['today_accuracy']}%")
    return result


async def get_weekly_trend(
    user_id: str,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    获取最近7天的复习趋势

    每天统计：
    - 复习次数
    - 正确次数
    - 正确率

    Args:
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        Dict: 7天趋势数据
    """
    now = datetime.now(timezone.utc)
    items = []
    total_reviews = 0
    total_correct = 0

    for i in range(6, -1, -1):
        day = now - timedelta(days=i)
        day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)

        day_total_result = await db.execute(
            select(func.count()).select_from(ReviewLog).where(
                ReviewLog.user_id == user_id,
                ReviewLog.review_at >= day_start,
                ReviewLog.review_at < day_end,
            )
        )
        day_total = day_total_result.scalar() or 0

        day_correct_result = await db.execute(
            select(func.count()).select_from(ReviewLog).where(
                ReviewLog.user_id == user_id,
                ReviewLog.review_at >= day_start,
                ReviewLog.review_at < day_end,
                ReviewLog.is_correct == True,
            )
        )
        day_correct = day_correct_result.scalar() or 0

        items.append({
            "date": day_start.strftime("%Y-%m-%d"),
            "review_count": day_total,
            "correct_count": day_correct,
            "accuracy": round(day_correct / day_total * 100, 1) if day_total > 0 else 0,
        })

        total_reviews += day_total
        total_correct += day_correct

    return {
        "items": items,
        "total_reviews": total_reviews,
        "avg_accuracy": round(total_correct / total_reviews * 100, 1) if total_reviews > 0 else 0,
    }


async def get_weak_points(
    user_id: str,
    db: AsyncSession,
    limit: int = 5,
) -> Dict[str, Any]:
    """
    获取薄弱点列表

    薄弱点定义：错误次数最多的知识卡片。
    统计每张卡片关联题目的错误次数，按错误次数降序排列。

    Args:
        user_id: 用户 ID
        db: 数据库会话
        limit: 返回数量上限

    Returns:
        Dict: 薄弱点列表
    """
    # 查询每张卡片的错误次数和总复习次数
    # 使用子查询统计
    error_counts = (
        select(
            QuizItem.card_id,
            func.count().label("total_reviews"),
            func.sum(case((ReviewLog.is_correct == False, 1), else_=0)).label("error_count"),
        )
        .join(ReviewLog, ReviewLog.quiz_id == QuizItem.id)
        .where(
            QuizItem.user_id == user_id,
            ReviewLog.user_id == user_id,
        )
        .group_by(QuizItem.card_id)
        .subquery()
    )

    # 关联知识卡片获取标题
    result = await db.execute(
        select(
            KnowledgeCard.id.label("card_id"),
            KnowledgeCard.title.label("card_title"),
            KnowledgeCard.card_type.label("card_type"),
            KnowledgeCard.note_id.label("note_id"),
            Note.title.label("note_title"),
            error_counts.c.error_count.label("error_count"),
            error_counts.c.total_reviews.label("total_reviews"),
        )
        .join(error_counts, error_counts.c.card_id == KnowledgeCard.id)
        .join(Note, Note.id == KnowledgeCard.note_id)
        .where(KnowledgeCard.user_id == user_id)
        .order_by(error_counts.c.error_count.desc())
        .limit(limit)
    )

    rows = result.all()
    items = []
    for row in rows:
        total = row.total_reviews or 0
        errors = row.error_count or 0
        items.append({
            "card_id": row.card_id,
            "card_title": row.card_title,
            "card_type": row.card_type.value if hasattr(row.card_type, 'value') else str(row.card_type),
            "note_id": row.note_id,
            "note_title": row.note_title or "",
            "error_count": errors,
            "total_reviews": total,
            "accuracy": round((total - errors) / total * 100, 1) if total > 0 else 0,
        })

    return {
        "items": items,
        "total": len(items),
    }
