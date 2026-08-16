"""
学习目标 API 模块

本模块提供学习目标管理与每日任务推荐相关的 HTTP 接口，所有接口均需要用户认证。

主要职责：
- 创建学习目标（POST /api/goals）
- 查询目标列表（GET /api/goals），支持按状态过滤
- 获取目标详情与进度（GET /api/goals/{goal_id}）
- 更新目标（PATCH /api/goals/{goal_id}）
- 归档目标（POST /api/goals/{goal_id}/archive）
- 软删除目标（DELETE /api/goals/{goal_id}）
- 获取今日推荐任务（GET /api/goals/daily-plan）

设计决策：
- 所有接口通过 get_current_user_dependency 确保用户已认证
- 目标查询自动过滤 user_id，确保用户只能访问自己的数据
- 软删除：DELETE 仅将 status 置为 DELETED，不物理删除记录
- /daily-plan 路由必须定义在 /{goal_id} 之前，否则 "daily-plan" 会被当作 goal_id 匹配
- 进度字段 progress_percentage 在详情接口中实时计算并填充到响应中
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.learning_goal import GoalStatus, GoalType, LearningGoal
from ..models.user import User
from ..api.auth import get_current_user_dependency
from ..schemas.goal import (
    DailyPlanResponse,
    GoalCreateRequest,
    GoalListResponse,
    GoalResponse,
    GoalUpdateRequest,
)
from ..services.goal_service import goal_service

router = APIRouter()


def _build_goal_response(goal: LearningGoal, progress: Optional[Dict[str, Any]] = None) -> GoalResponse:
    """
    构建 GoalResponse，可选填充进度信息

    Args:
        goal: 学习目标 ORM 对象
        progress: 进度字典（来自 goal_service.get_goal_progress），可选。
                  若提供则将 progress_percentage 填充到响应中。

    Returns:
        GoalResponse: 学习目标响应对象
    """
    resp = GoalResponse.model_validate(goal)
    if progress is not None:
        resp.progress_percentage = float(progress.get("progress_percentage", 0.0))
    else:
        # F-13 修复：列表接口无实时进度时回退 progress_cache（Beat 每日刷新），
        # 避免列表进度恒为 0
        resp.progress_percentage = float(getattr(goal, "progress_cache", 0.0) or 0.0)
    return resp


@router.post("", response_model=GoalResponse, status_code=status.HTTP_201_CREATED)
async def create_goal(
    req: GoalCreateRequest,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    创建学习目标

    每用户最多 5 个活跃目标，超过上限时返回 400 错误。

    Args:
        req: 创建请求体，包含 name / type / scope_notes / scope_folders /
             target_mastery / deadline
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        GoalResponse: 新创建的目标信息

    Raises:
        HTTPException 400: 活跃目标数已达上限（5 个）
    """
    # goal_service.create_goal 内部会在活跃目标数超限时抛出 HTTPException(400)
    goal = await goal_service.create_goal(current_user.id, req, db)
    return _build_goal_response(goal)


@router.get("", response_model=GoalListResponse)
async def list_goals(
    status_filter: Optional[str] = Query(None, alias="status", description="状态过滤：active / completed / expired / archived / deleted"),
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取学习目标列表

    默认排除已软删除的目标，可通过 status 参数过滤特定状态。

    Args:
        status_filter: 状态过滤参数（query alias=status），为 None 时排除 DELETED
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        GoalListResponse: 包含目标列表和总数的响应
    """
    goals = await goal_service.list_goals(current_user.id, status_filter, db)
    return GoalListResponse(
        goals=[_build_goal_response(g) for g in goals],
        total=len(goals),
    )


@router.get("/daily-plan", response_model=DailyPlanResponse)
async def get_daily_plan(
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取今日推荐任务

    若今日已有计划则直接返回，否则基于活跃目标的 scope_notes 生成新计划。
    任务分为三类：薄弱点（weak_points）、到期复习（review）、新资料（new_materials）。

    注意：该路由必须定义在 /{goal_id} 之前，否则 "daily-plan" 会被当作 goal_id 匹配。

    Args:
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        DailyPlanResponse: 今日计划，包含推荐任务和完成进度

    Raises:
        HTTPException 400: 用户无活跃目标，需先创建目标
    """
    # generate_daily_plan 内部会在用户无活跃目标时抛出 HTTPException(400)
    plan = await goal_service.generate_daily_plan(current_user.id, db)
    return DailyPlanResponse.model_validate(plan)


@router.get("/{goal_id}", response_model=GoalResponse)
async def get_goal(
    goal_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取学习目标详情（含实时进度）

    返回目标基本信息以及计算得出的 progress_percentage（0-100）。
    进度计算基于范围内卡片的平均掌握度 / 目标掌握度。

    Args:
        goal_id: 目标 ID
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        GoalResponse: 目标详情，含 progress_percentage 字段

    Raises:
        HTTPException 404: 目标不存在或不属于当前用户
    """
    # get_goal 内部会在目标不存在时抛出 HTTPException(404)
    goal = await goal_service.get_goal(goal_id, current_user.id, db)
    progress = await goal_service.get_goal_progress(goal_id, current_user.id, db)
    return _build_goal_response(goal, progress)


@router.patch("/{goal_id}", response_model=GoalResponse)
async def update_goal(
    goal_id: str,
    req: GoalUpdateRequest,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    更新学习目标

    所有字段均为可选更新，仅更新请求中显式提供的字段。

    Args:
        goal_id: 目标 ID
        req: 更新请求体，含可选字段 name / type / scope_notes / scope_folders /
             target_mastery / deadline / status
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        GoalResponse: 更新后的目标信息

    Raises:
        HTTPException 404: 目标不存在或不属于当前用户
    """
    # get_goal 内部会在目标不存在时抛出 HTTPException(404)
    goal = await goal_service.get_goal(goal_id, current_user.id, db)

    # F-09 修复：更新 scope 时校验归属，防止引用他人笔记/文件夹（IDOR）
    if req.scope_notes is not None or req.scope_folders is not None:
        from ..services.goal_service import _validate_goal_scopes
        await _validate_goal_scopes(
            db,
            current_user.id,
            list(req.scope_notes) if req.scope_notes is not None else (goal.scope_notes or []),
            list(req.scope_folders) if req.scope_folders is not None else (goal.scope_folders or []),
        )

    # 按字段更新：仅更新请求中显式提供的非 None 字段
    # name 字段使用 Pydantic 的 min_length=1 校验，已保证非空
    if req.name is not None:
        goal.name = req.name
    if req.type is not None:
        # GoalType 是枚举，取 .value 存储为字符串
        goal.type = req.type.value if hasattr(req.type, "value") else req.type
    if req.scope_notes is not None:
        goal.scope_notes = list(req.scope_notes)
    if req.scope_folders is not None:
        goal.scope_folders = list(req.scope_folders)
    if req.target_mastery is not None:
        goal.target_mastery = float(req.target_mastery)
    if req.deadline is not None:
        goal.deadline = req.deadline
    if req.status is not None:
        goal.status = req.status.value if hasattr(req.status, "value") else req.status

    await db.commit()
    await db.refresh(goal)
    return _build_goal_response(goal)


@router.post("/{goal_id}/archive", response_model=GoalResponse)
async def archive_goal(
    goal_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    归档学习目标

    将目标状态置为 ARCHIVED，归档后目标不再参与每日计划生成和进度刷新。

    Args:
        goal_id: 目标 ID
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        GoalResponse: 更新后的目标信息

    Raises:
        HTTPException 404: 目标不存在或不属于当前用户
    """
    # archive_goal 内部会在目标不存在时抛出 HTTPException(404)
    goal = await goal_service.archive_goal(goal_id, current_user.id, db)
    return _build_goal_response(goal)


@router.delete("/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_goal(
    goal_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    软删除学习目标

    将目标状态置为 DELETED，不物理删除记录。
    软删除后目标在列表查询中默认不可见，但仍可通过 status=deleted 过滤参数访问。

    Args:
        goal_id: 目标 ID
        current_user: 当前认证用户
        db: 异步数据库会话

    Raises:
        HTTPException 404: 目标不存在或不属于当前用户
    """
    # delete_goal 内部会在目标不存在时抛出 HTTPException(404)
    await goal_service.delete_goal(goal_id, current_user.id, db)


if __name__ == "__main__":
    pass
