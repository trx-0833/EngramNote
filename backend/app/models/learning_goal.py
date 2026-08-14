"""
学习目标模型模块

本模块定义了学习目标与每日计划的数据表模型，支持用户设定学习目标并获取每日任务推荐。

主要职责：
- 定义目标类型枚举 GoalType（每日 / 每周）
- 定义目标状态枚举 GoalStatus（active / completed / expired / archived / deleted）
- 定义学习目标表结构，关联用户、范围（笔记/文件夹）、目标掌握度和截止时间
- 定义每日计划表结构，存储推荐任务和完成进度

设计决策：
- scope_notes / scope_folders 使用 JSON 数组存储范围 ID 列表，灵活支持多选
- recommended_tasks 使用 JSON 字典存储分类任务（review / new_materials / weak_points）
- progress_cache 缓存完成百分比，由 Celery Beat 定时刷新，避免实时计算开销
- 软删除：status=DELETED 保留记录，不物理删除
- 继承 BaseModel 获得 id、created_at、updated_at 公共字段
"""

import enum
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import String, Integer, Float, Text, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel, TZDateTime


class GoalType(str, enum.Enum):
    """
    学习目标类型枚举

    标识目标的周期，决定推荐任务的粒度：
    - daily: 每日目标，聚焦当天任务
    - weekly: 每周目标，覆盖一周学习计划
    """
    DAILY = "daily"
    WEEKLY = "weekly"


class GoalStatus(str, enum.Enum):
    """
    学习目标状态枚举

    标识目标的生命周期状态：
    - active: 进行中
    - completed: 已完成（达到目标掌握度）
    - expired: 已过期（超过截止时间）
    - archived: 已归档（用户主动归档）
    - deleted: 已删除（软删除）
    """
    ACTIVE = "active"
    COMPLETED = "completed"
    EXPIRED = "expired"
    ARCHIVED = "archived"
    DELETED = "deleted"


class LearningGoal(BaseModel):
    """
    学习目标模型

    对应数据库中的 learning_goals 表，存储用户设定的学习目标。

    Attributes:
        id: UUID 主键（继承自 BaseModel）
        user_id: 所属用户 ID，外键关联 users 表
        name: 目标名称，如 "本周掌握第三章"
        type: 目标类型（daily / weekly）
        scope_notes: 目标范围包含的笔记 ID 列表
        scope_folders: 目标范围包含的文件夹 ID 列表
        target_mastery: 目标掌握度（0-100），默认 80.0
        deadline: 截止时间，可选
        status: 目标状态，默认 active
        progress_cache: 缓存的完成百分比，由定时任务刷新
        last_progress_refresh: 上次刷新进度的时间
        created_at: 创建时间（继承自 BaseModel）
        updated_at: 更新时间（继承自 BaseModel）
    """
    __tablename__ = "learning_goals"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False, default=GoalType.WEEKLY.value)
    # 目标范围包含的笔记 ID 列表
    scope_notes: Mapped[List[str]] = mapped_column(JSON, default=list)
    # 目标范围包含的文件夹 ID 列表
    scope_folders: Mapped[List[str]] = mapped_column(JSON, default=list)
    target_mastery: Mapped[float] = mapped_column(Float, default=80.0)
    deadline: Mapped[Optional[datetime]] = mapped_column(TZDateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=GoalStatus.ACTIVE.value, index=True)
    # 缓存的完成百分比，由 Celery Beat 定时刷新
    progress_cache: Mapped[float] = mapped_column(Float, default=0.0)
    last_progress_refresh: Mapped[Optional[datetime]] = mapped_column(TZDateTime(timezone=True), nullable=True)


class DailyPlan(BaseModel):
    """
    每日计划模型

    对应数据库中的 daily_plans 表，存储基于用户活跃目标生成的每日任务推荐。

    Attributes:
        id: UUID 主键（继承自 BaseModel）
        goal_id: 关联学习目标 ID，外键关联 learning_goals 表
        user_id: 所属用户 ID，外键关联 users 表
        plan_date: 计划日期
        recommended_tasks: 推荐任务字典，包含 review / new_materials / weak_points 三类
        completed_count: 已完成任务数
        total_count: 总任务数
        created_at: 创建时间（继承自 BaseModel）
        updated_at: 更新时间（继承自 BaseModel）
    """
    __tablename__ = "daily_plans"

    goal_id: Mapped[str] = mapped_column(String(36), ForeignKey("learning_goals.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    plan_date: Mapped[datetime] = mapped_column(TZDateTime(timezone=True), nullable=False, index=True)
    # 推荐任务：{"review": [...], "new_materials": [...], "weak_points": [...]}
    recommended_tasks: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    completed_count: Mapped[int] = mapped_column(Integer, default=0)
    total_count: Mapped[int] = mapped_column(Integer, default=0)
