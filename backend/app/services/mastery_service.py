"""
掌握度计算服务模块

基于卡片的复习表现（SM-2 参数 + ReviewLog 正确率）计算 0-100 的掌握度。
公式（Q8 已确认）：mastery = 正确率 × 60% + 标准化 SM-2 状态 × 40%
- 正确率 = 最近 5 次 ReviewLog 中正确次数 / min(5, 实际次数)
- 标准化 SM-2 = (easiness_factor - 1.3) / (2.8 - 1.3) × 0.5 + min(repetition / 10, 1) × 0.5，裁剪到 0-1
- 未复习过的卡片 mastery_level = 0
"""

import logging
from typing import Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.knowledge_card import KnowledgeCard
from ..models.quiz_item import QuizItem
from ..models.review_log import ReviewLog

logger = logging.getLogger(__name__)


async def compute_card_mastery(card_id: str, db: AsyncSession) -> float:
    """
    计算指定卡片的掌握度（0-100）

    Args:
        card_id: 知识卡片 ID
        db: 数据库会话

    Returns:
        float: 掌握度（0-100，保留 1 位小数）
    """
    # 1. 查询该卡片下所有 QuizItem 的 id 列表
    quiz_id_result = await db.execute(
        select(QuizItem.id).where(QuizItem.card_id == card_id)
    )
    quiz_ids = [row[0] for row in quiz_id_result.all()]

    # 卡片下无题目，无法计算
    if not quiz_ids:
        return 0.0

    # 2. 查询这些 QuizItem 关联的最近 5 条 ReviewLog，Python 端统计正确数
    review_result = await db.execute(
        select(ReviewLog)
        .where(ReviewLog.quiz_id.in_(quiz_ids))
        .order_by(ReviewLog.review_at.desc())
        .limit(5)
    )
    recent_reviews = list(review_result.scalars().all())

    # 未复习过的卡片 mastery_level = 0
    if not recent_reviews:
        return 0.0

    actual_count = len(recent_reviews)
    correct_count = sum(1 for r in recent_reviews if r.is_correct)
    accuracy = correct_count / min(5, actual_count)

    # 3. 聚合查询该卡片下所有 QuizItem 的 easiness_factor 和 repetition 的平均值
    agg_result = await db.execute(
        select(
            func.avg(QuizItem.easiness_factor),
            func.avg(QuizItem.repetition),
        ).where(QuizItem.card_id == card_id)
    )
    agg_row = agg_result.one()
    avg_ef = float(agg_row[0]) if agg_row[0] is not None else 2.5
    avg_rep = float(agg_row[1]) if agg_row[1] is not None else 0.0

    # 4. 标准化 SM-2 状态（裁剪到 0-1）
    ef_norm = (avg_ef - 1.3) / (2.8 - 1.3)
    ef_norm = max(0.0, min(1.0, ef_norm))
    rep_norm = avg_rep / 10
    rep_norm = max(0.0, min(1.0, rep_norm))
    normalized_sm2 = ef_norm * 0.5 + rep_norm * 0.5

    # 5. 计算掌握度（裁剪到 0-100，保留 1 位小数）
    mastery = (accuracy * 0.6 + normalized_sm2 * 0.4) * 100
    mastery = max(0.0, min(100.0, mastery))
    return round(mastery, 1)


async def refresh_card_mastery(card_id: str, db: AsyncSession) -> None:
    """
    刷新指定卡片的掌握度（计算后写入 KnowledgeCard.mastery_level）

    本函数内部捕获所有异常，仅记 warning 日志，不抛出，
    以确保调用方（如答题流程）不受影响。

    Args:
        card_id: 知识卡片 ID
        db: 数据库会话
    """
    try:
        mastery = await compute_card_mastery(card_id, db)

        # 先 select 再 setattr 再 commit，确保使用当前会话
        result = await db.execute(
            select(KnowledgeCard).where(KnowledgeCard.id == card_id)
        )
        card = result.scalars().first()
        if card is None:
            logger.warning(f"刷新掌握度失败：卡片不存在 (card_id={card_id})")
            return

        card.mastery_level = mastery
        await db.commit()
    except Exception as e:
        logger.warning(f"刷新卡片掌握度失败 (card_id={card_id}): {e}")
        return
