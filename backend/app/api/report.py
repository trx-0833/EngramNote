"""
学习报告 API 模块

本模块提供学习报告相关的 HTTP 接口，包括今日报告、
7天趋势和薄弱点分析等。

主要职责：
- 获取今日学习报告（GET /api/report/daily）
- 获取7天趋势数据（GET /api/report/weekly-trend）
- 获取薄弱点列表（GET /api/report/weak-points）
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.user import User
from ..api.auth import get_current_user_dependency
from ..schemas.report import (
    DailyReportResponse,
    WeeklyTrendResponse,
    WeakPointsResponse,
)
from ..services import report_service

router = APIRouter()


@router.get("/daily", response_model=DailyReportResponse)
async def get_daily_report(
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取今日学习报告

    包含今日新掌握知识点数、复习时长、正确率、各题型正确率等。
    """
    result = await report_service.get_daily_report(
        user_id=current_user.id,
        db=db,
    )
    return DailyReportResponse(**result)


@router.get("/weekly-trend", response_model=WeeklyTrendResponse)
async def get_weekly_trend(
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取最近7天复习趋势

    每天的复习次数、正确次数和正确率。
    """
    result = await report_service.get_weekly_trend(
        user_id=current_user.id,
        db=db,
    )
    return WeeklyTrendResponse(**result)


@router.get("/weak-points", response_model=WeakPointsResponse)
async def get_weak_points(
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(5, ge=1, le=20),
):
    """
    获取薄弱点列表

    按错误次数降序排列的知识卡片，错误越多越薄弱。
    """
    result = await report_service.get_weak_points(
        user_id=current_user.id,
        db=db,
        limit=limit,
    )
    return WeakPointsResponse(**result)
