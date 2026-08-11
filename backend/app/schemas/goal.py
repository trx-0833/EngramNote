"""
学习目标 Pydantic Schema

定义学习目标管理与每日任务推荐相关的请求/响应模型。
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..models.learning_goal import GoalType, GoalStatus


# --- 推荐任务模型 ---

class RecommendedTask(BaseModel):
    """
    推荐任务条目

    表示每日计划中的一条具体任务，可能是复习题、新资料或薄弱点练习。

    Attributes:
        task_type: 任务类型（review / new_material / weak_point）
        quiz_id: 关联题目 ID，可选（复习/薄弱点任务可能有）
        note_id: 关联笔记 ID，可选（新资料任务可能有）
        card_id: 关联知识卡片 ID，可选
        priority: 优先级（数值越大越优先），weak_point=3 > review=2 > new_material=1
        title: 任务标题，用于前端展示
    """
    task_type: str = Field(..., description="任务类型：review / new_material / weak_point")
    quiz_id: Optional[str] = None
    note_id: Optional[str] = None
    card_id: Optional[str] = None
    priority: int = Field(..., ge=1, le=3, description="优先级 1-3，数值越大越优先")
    title: str = Field(..., min_length=1, max_length=500)


# --- 请求模型 ---

class GoalCreateRequest(BaseModel):
    """
    学习目标创建请求

    Attributes:
        name: 目标名称，1-200 字符
        type: 目标类型（daily / weekly）
        scope_notes: 目标范围包含的笔记 ID 列表
        scope_folders: 目标范围包含的文件夹 ID 列表
        target_mastery: 目标掌握度（0-100），默认 80.0
        deadline: 截止时间，可选
    """
    name: str = Field(..., min_length=1, max_length=200)
    type: GoalType = GoalType.WEEKLY
    scope_notes: List[str] = Field(default_factory=list)
    scope_folders: List[str] = Field(default_factory=list)
    target_mastery: float = Field(80.0, ge=0, le=100)
    deadline: Optional[datetime] = None


class GoalUpdateRequest(BaseModel):
    """
    学习目标更新请求（所有字段可选）

    Attributes:
        name: 目标名称
        type: 目标类型
        scope_notes: 目标范围包含的笔记 ID 列表
        scope_folders: 目标范围包含的文件夹 ID 列表
        target_mastery: 目标掌握度（0-100）
        deadline: 截止时间
        status: 目标状态
    """
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    type: Optional[GoalType] = None
    scope_notes: Optional[List[str]] = None
    scope_folders: Optional[List[str]] = None
    target_mastery: Optional[float] = Field(None, ge=0, le=100)
    deadline: Optional[datetime] = None
    status: Optional[GoalStatus] = None


# --- 响应模型 ---

class GoalResponse(BaseModel):
    """
    学习目标响应

    包含 LearningGoal 的所有字段，以及计算得出的 progress_percentage。

    Attributes:
        id: 目标 ID
        user_id: 所属用户 ID
        name: 目标名称
        type: 目标类型
        scope_notes: 范围笔记 ID 列表
        scope_folders: 范围文件夹 ID 列表
        target_mastery: 目标掌握度
        deadline: 截止时间
        status: 目标状态
        progress_cache: 缓存的完成百分比
        last_progress_refresh: 上次刷新进度的时间
        progress_percentage: 实时计算的完成百分比（0-100）
        created_at: 创建时间
        updated_at: 更新时间
    """
    id: str
    user_id: str
    name: str
    type: GoalType
    scope_notes: List[str] = []
    scope_folders: List[str] = []
    target_mastery: float
    deadline: Optional[datetime] = None
    status: GoalStatus
    progress_cache: float = 0.0
    last_progress_refresh: Optional[datetime] = None
    progress_percentage: float = 0.0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class GoalListResponse(BaseModel):
    """
    学习目标列表响应

    Attributes:
        goals: 目标列表
        total: 总数
    """
    goals: List[GoalResponse]
    total: int


class GoalProgressResponse(BaseModel):
    """
    学习目标进度响应

    Attributes:
        goal_id: 目标 ID
        progress_percentage: 完成百分比（0-100）
        avg_mastery: 范围内卡片的平均掌握度（0-100）
        reviewed_count: 今日已复习题目数
        total_count: 范围内总题目数
        days_remaining: 剩余天数（无截止时间时为 None）
    """
    goal_id: str
    progress_percentage: float
    avg_mastery: float
    reviewed_count: int
    total_count: int
    days_remaining: Optional[int] = None


class DailyPlanResponse(BaseModel):
    """
    每日计划响应

    Attributes:
        id: 计划 ID
        goal_id: 关联目标 ID
        plan_date: 计划日期
        recommended_tasks: 推荐任务字典，包含 review / new_materials / weak_points 三类
        completed_count: 已完成任务数
        total_count: 总任务数
    """
    id: str
    goal_id: str
    plan_date: datetime
    recommended_tasks: Dict[str, Any] = {}
    completed_count: int = 0
    total_count: int = 0

    model_config = {"from_attributes": True}
