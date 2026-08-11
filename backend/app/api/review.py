"""
复习调度 API 模块

本模块提供复习调度相关的 HTTP 接口，包括获取到期题目、
提交答案、查看复习统计和复习历史等操作。

主要职责：
- 获取今日到期题目（GET /api/review/due）
- 提交答案（POST /api/review/submit）
- 获取复习统计（GET /api/review/stats）
- 获取复习历史（GET /api/review/history）
- 获取复习提醒概览（GET /api/review/reminders）

设计决策：
- 所有接口需要用户认证，且只能操作自己的数据
- 提交答案后即时返回判分结果和 SM-2 更新信息
- 到期题目按 next_review_at 升序排列
- /reminders 端点放在所有路径参数路由之前，避免 "reminders" 被识别为 review_id
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.user import User
from ..api.auth import get_current_user_dependency
from ..schemas.review import (
    DueQuizResponse,
    DueQuizListResponse,
    SubmitAnswerRequest,
    SubmitAnswerResponse,
    ReviewStatsResponse,
    ReviewHistoryResponse,
    ReminderResponse,
)
from ..services import review_service
from ..services.notification_service import NotificationService

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
    """
    result = await review_service.submit_answer(
        quiz_id=req.quiz_id,
        user_id=current_user.id,
        user_answer=req.user_answer,
        time_spent_ms=req.time_spent_ms,
        db=db,
    )

    if "error" in result:
        if "上限" in result["error"]:
            raise HTTPException(status_code=429, detail=result["error"])
        raise HTTPException(status_code=404, detail=result["error"])

    return SubmitAnswerResponse(
        quiz_id=result["quiz_id"],
        is_correct=result["is_correct"],
        quality=result["quality"],
        correct_answer=result["correct_answer"],
        explanation=result.get("explanation"),
        options=result.get("options"),
        question_type=result.get("question_type", "choice"),
        sm2=result["sm2"],
    )


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
        )
