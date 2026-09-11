"""
复习调度 API 模块

本模块提供复习调度相关的 HTTP 接口，包括获取到期题目、
提交答案、查看复习统计和复习历史等操作。

主要职责：
- 获取今日到期题目（GET /api/review/due）
- 提交答案（POST /api/review/submit）
- **卡片直接复习**（GET /api/review/cards/due、POST /api/review/cards/{id}/submit）
- 获取复习统计（GET /api/review/stats）
- 获取复习历史（GET /api/review/history）
- 获取复习提醒概览（GET /api/review/reminders）

设计决策：
- 所有接口需要用户认证，且只能操作自己的数据
- 提交答案后即时返回判分结果和 SM-2 更新信息
- 到期题目按 next_review_at 升序排列
- /reminders 端点放在所有路径参数路由之前，避免 "reminders" 被识别为 review_id
- 卡片复习与答题复习是**两条并行路径**：卡片复习没有题目，走四档自评，
  不受每日答题限额约束（限额针对答题）
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.knowledge_card import KnowledgeCard
from ..models.note import Note
from ..models.review_log import ReviewLog
from ..models.user import User
from ..api.auth import get_current_user_dependency
from ..models.review_state import ITEM_TYPE_CARD
from ..schemas.review import (
    CardReviewItem,
    CardReviewListResponse,
    CardReviewSubmitRequest,
    CardReviewSubmitResponse,
    DueQuizResponse,
    DueQuizListResponse,
    SubmitAnswerRequest,
    SubmitAnswerResponse,
    ReviewStatsResponse,
    ReviewHistoryResponse,
    ReminderResponse,
)
from ..services import mastery_service, review_service, review_state_service
from ..services.notification_service import NotificationService
from ..services.sm2_service import calculate_sm2

router = APIRouter()

# 模块级通知服务实例，避免每次请求重复创建
_notification_service = NotificationService()
logger = logging.getLogger(__name__)


@router.get("/due", response_model=DueQuizListResponse)
async def get_due_quizzes(
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
):
    """
    获取今日到期复习题目

    返回 next_review_at <= 当前时间的题目（含新题目），
    按到期时间升序排列，最过期的优先。
    """
    quizzes = await review_service.get_due_quizzes(
        user_id=current_user.id,
        db=db,
        limit=limit,
    )
    return DueQuizListResponse(
        items=[DueQuizResponse.model_validate(q) for q in quizzes],
        total=len(quizzes),
    )


@router.post("/submit", response_model=SubmitAnswerResponse)
async def submit_answer(
    req: SubmitAnswerRequest,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    提交答案

    判断正误，更新 SM-2 调度参数，记录复习日志。
    返回判分结果和下次复习时间。

    简答题的自动判分不可信：首次提交（不带 self_rating）只落一条占位记录、
    不推进调度，响应中 needs_self_assessment=true；用户自评后再次提交
    （带 self_rating）才真正推进 SM-2。
    """
    result = await review_service.submit_answer(
        quiz_id=req.quiz_id,
        user_id=current_user.id,
        user_answer=req.user_answer,
        time_spent_ms=req.time_spent_ms,
        db=db,
        self_rating=req.self_rating,
        use_semantic_grading=req.use_semantic_grading,
    )

    if "error" in result:
        if "上限" in result["error"]:
            raise HTTPException(status_code=429, detail=result["error"])
        raise HTTPException(status_code=404, detail=result["error"])

    return SubmitAnswerResponse.from_service_result(result)


@router.get("/stats", response_model=ReviewStatsResponse)
async def get_review_stats(
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取复习统计数据

    包括今日待复习数、已完成数、正确率，以及累计统计。
    """
    stats = await review_service.get_review_stats(
        user_id=current_user.id,
        db=db,
    )
    return ReviewStatsResponse(**stats)


@router.get("/history", response_model=ReviewHistoryResponse)
async def get_review_history(
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """
    获取复习历史（分页）

    按答题时间降序排列，最新的在前。
    """
    result = await review_service.get_review_history(
        user_id=current_user.id,
        db=db,
        page=page,
        page_size=page_size,
    )
    return ReviewHistoryResponse(**result)


@router.get("/cards/due", response_model=CardReviewListResponse)
async def list_due_cards(
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """列出当前到期可复习的**卡片**（阶段 3.12：卡片可直接复习）

    解决的问题（overhaul-plan 症状 L-5）：旧模型下调度参数只存在于
    `quiz_items` 上，因此**没有生成过题目的卡片永远无法进入复习队列**
    —— 实测 1183 张卡片中大量卡片从未被复习过。

    本接口从 `review_states` 读取 `item_type='card'` 的到期项，
    与"答题复习"是并行的两条路径。

    注意：只返回已存在于 `review_states` 的卡片。存量数据需先跑
    `scripts/migrate_review_states.py` 补建。
    """
    states = await review_state_service.list_due_states(
        db, current_user.id, item_type=ITEM_TYPE_CARD, limit=limit,
    )
    if not states:
        return CardReviewListResponse(items=[], total=0)

    card_ids = [s.item_id for s in states]
    cards = {
        c.id: c
        for c in (await db.execute(
            select(KnowledgeCard).where(
                KnowledgeCard.id.in_(card_ids),
                KnowledgeCard.user_id == current_user.id,
                # 回收站笔记的卡片不进复习队列
                or_(
                    KnowledgeCard.note_id.is_(None),
                    select(Note.id).where(
                        Note.id == KnowledgeCard.note_id,
                        Note.trashed_at.is_(None),
                    ).exists(),
                ),
            )
        )).scalars().all()
    }

    items: list[CardReviewItem] = []
    for state in states:
        card = cards.get(state.item_id)
        if card is None:
            # 卡片已删或笔记进了回收站：跳过（不改状态，删除时另行清理）
            continue
        items.append(CardReviewItem(
            card_id=card.id,
            title=card.title,
            content=card.content,
            summary=card.summary,
            card_type=card.card_type.value if hasattr(card.card_type, "value") else str(card.card_type),
            chapter_title=card.chapter_title,
            note_id=card.note_id,
            mastery_level=float(card.mastery_level or 0.0),
            interval_days=state.interval_days,
            repetition=state.repetition,
            easiness_factor=state.easiness_factor,
            next_review_at=state.next_review_at,
            review_count=state.review_count,
            lapses=state.lapses,
        ))

    return CardReviewListResponse(items=items, total=len(items))


@router.post("/cards/{card_id}/submit", response_model=CardReviewSubmitResponse)
async def submit_card_review(
    card_id: str,
    req: CardReviewSubmitRequest,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """提交一次**卡片级**复习（阶段 3.12）

    与 `/review/submit` 的区别：这里没有题目，用户直接对卡片自评
    "想起来没有"（四档）。这对**没有生成过题目的卡片**是唯一的复习途径。

    流程：
    1. 校验卡片归属
    2. 用四档自评作为 quality 跑 SM-2
    3. 双写 review_states（卡片维度）与 ReviewLog（quiz_id 为空）
    4. 刷新卡片掌握度

    设计决策：
    - 不检查每日答题上限：卡片复习是"轻量回顾"，与答题限额分开计（限额针对答题）
    - `quiz_id` 允许为空（见 ReviewLog 模型的说明）：卡片级复习没有题目
    """
    card = (await db.execute(
        select(KnowledgeCard).where(
            KnowledgeCard.id == card_id,
            KnowledgeCard.user_id == current_user.id,
        )
    )).scalars().first()
    if card is None:
        raise HTTPException(status_code=404, detail="卡片不存在")

    state = await review_state_service.get_state(
        db, current_user.id, ITEM_TYPE_CARD, card_id,
    )
    if state is None:
        raise HTTPException(status_code=404, detail="卡片复习状态不可用")

    quality = max(0, min(5, int(req.self_rating)))
    sm2_result = calculate_sm2(
        quality=quality,
        interval=state.interval_days,
        repetition=state.repetition,
        easiness_factor=state.easiness_factor,
    )

    now = datetime.now(timezone.utc)
    await review_state_service.apply_sm2_result(
        db, current_user.id, ITEM_TYPE_CARD, card_id,
        quality=quality,
        interval_days=sm2_result.interval,
        repetition=sm2_result.repetition,
        easiness_factor=sm2_result.easiness_factor,
        next_review_at=sm2_result.next_review_at,
        now=now,
    )

    # 事件流：quiz_id 为空表示卡片级复习；card_id 让记录能归到具体卡片
    db.add(ReviewLog(
        user_id=current_user.id,
        quiz_id=None,
        card_id=card_id,
        note_id=card.note_id,
        user_answer=req.user_answer or "",
        is_correct=quality >= 3,
        quality=quality,
        self_rating=quality,
        grading_method="self_rating",
        time_spent_ms=req.time_spent_ms,
        review_at=now,
    ))
    await db.commit()

    await mastery_service.refresh_card_mastery(card_id, db, user_id=current_user.id)

    return CardReviewSubmitResponse(
        card_id=card_id,
        quality=quality,
        is_correct=quality >= 3,
        interval_days=sm2_result.interval,
        repetition=sm2_result.repetition,
        easiness_factor=sm2_result.easiness_factor,
        next_review_at=sm2_result.next_review_at,
        mastery_level=float(card.mastery_level or 0.0),
    )


@router.get("/reminders", response_model=ReminderResponse)
async def get_reminders(
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取复习提醒概览

    返回当前用户的复习提醒数据，包括：
    - due_count: 已到期或尚未安排复习的题目数
    - due_in_1h_count: 未来 1 小时内到期的题目数
    - weak_point_count: 掌握度低于 60 的知识卡片数
    - last_reminded_at: 上次提醒时间（暂未持久化，固定为 None）

    注意：本路由需放在任何 /{review_id} 路径参数路由之前，
    避免 "reminders" 被识别为 review_id。
    """
    try:
        reminders = await _notification_service.get_reminders(
            user_id=current_user.id, db=db
        )
        return ReminderResponse(**reminders)
    except HTTPException:
        # 透传已知的 HTTP 异常
        raise
    except Exception as e:
        logger.error(
            "获取复习提醒失败: user_id=%s, err=%s",
            current_user.id, e,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail="获取复习提醒数据失败"
        ) from e
