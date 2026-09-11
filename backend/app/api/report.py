"""
学习报告 API 模块

本模块提供学习报告相关的 HTTP 接口，包括今日报告、
7天趋势、薄弱点分析和学习度量等。

主要职责：
- 获取今日学习报告（GET /api/report/daily）
- 获取7天趋势数据（GET /api/report/weekly-trend）
- 获取薄弱点列表（GET /api/report/weak-points）
- 获取学习度量（GET /api/report/learning-metrics，阶段 3.14）
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.user import User
from ..api.auth import get_current_user_dependency
from ..schemas.report import (
    DailyReportResponse,
    LearningMetricsResponse,
    WeeklyTrendResponse,
    WeakPointsResponse,
)
from ..services import learning_metrics_service, report_service

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


@router.get("/learning-metrics", response_model=LearningMetricsResponse)
async def get_learning_metrics(
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """获取学习度量（阶段 3.14）

    返回四类度量与**数据质量说明**：

    - `retention`：保持率曲线（复习后 t 天还能回忆的比例）
    - `calibration`：校准曲线（自评说"想起来了"时是否真的想起来）
    - `lapses`：遗忘分布（反复忘的卡片，leech 候选）
    - `load` / `forecast`：未来到期量

    **每个曲线都带 `insufficient_data` 与 `min_sample`。** 这不是可选装饰：
    现场实测当前库里「间隔 > 0 的相邻复习对」为 0 条、自评样本为 0 条，
    若只返回曲线数据，前端会画出空图或由噪声决定的假图。
    `data_quality.notes` 直接给出面向用户的说明（缺什么、怎么才会有）。
    """
    metrics = await learning_metrics_service.get_learning_metrics(db, current_user.id)
    return LearningMetricsResponse(**metrics)
