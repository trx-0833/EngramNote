"""
复习调度 API 模块

本模块提供复习调度相关的 HTTP 接口，包括获取到期题目、
提交答案、查看复习统计和复习历史等操作。

主要职责：
- 获取今日到期题目（GET /api/review/due）
- 提交答案（POST /api/review/submit）
- 获取复习统计（GET /api/review/stats）
- 获取复习历史（GET /api/review/history）

设计决策：
- 所有接口需要用户认证，且只能操作自己的数据
- 提交答案后即时返回判分结果和 SM-2 更新信息
- 到期题目按 next_review_at 升序排列
"""

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
)
from ..services import review_service

router = APIRouter()


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
