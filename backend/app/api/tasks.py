"""
任务进度 API 模块

为前端提供"任务现在做到哪一步"的查询与取消入口（阶段 1′ 1.7）。

主要职责：
- GET  /api/tasks/{task_id}        查询单个任务状态与进度
- GET  /api/tasks/note/{note_id}   列出某笔记的近期任务（供详情页轮询）
- POST /api/tasks/{task_id}/cancel 请求取消

设计决策：
- 归属校验：任务记录带 user_id，查询时校验当前用户，防止用 task_id 探测他人任务
- 取消是**尽力而为**：文件 broker 不支持可靠强杀，这里只把记录置为
  cancelled 让 UI 停止转圈，任务自身在下一个阶段边界检查到后主动退出
  （见 task_run_service.request_cancel 的说明）。接口如实返回这一点，
  不谎称"已终止"。
"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.auth import get_current_user_dependency
from ..core.app_error import TASK_NOT_FOUND, AppError
from ..database import get_db
from ..models.task_run import TaskRun
from ..models.user import User
from ..services import task_run_service

logger = logging.getLogger(__name__)

router = APIRouter()


class TaskRunResponse(BaseModel):
    """任务运行状态响应"""
    task_id: str = Field(description="Celery 任务 ID")
    task_name: str = Field(description="任务全名")
    note_id: Optional[str] = Field(default=None, description="关联笔记 ID")
    status: str = Field(description="pending/running/succeeded/failed/stale/cancelled")
    progress: float = Field(description="完成度 0.0~1.0")
    stage: str = Field(default="", description="当前阶段的可读名称，可直接展示")
    message: Optional[str] = Field(default=None, description="附加说明")
    attempt: int = Field(description="第几次尝试（从 1 开始）")
    max_attempts: int = Field(description="允许的最大尝试次数")
    error: Optional[str] = Field(default=None, description="失败摘要（已截断）")
    retryable: bool = Field(
        default=False,
        description="是否可重试。失败/僵尸且未超过最大尝试次数时为 true",
    )
    heartbeat_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    @classmethod
    def from_model(cls, run: TaskRun) -> "TaskRunResponse":
        return cls(
            task_id=run.task_id,
            task_name=run.task_name,
            note_id=run.note_id,
            status=run.status.value,
            progress=run.progress,
            stage=run.stage or "",
            message=run.message,
            attempt=run.attempt,
            max_attempts=run.max_attempts,
            error=run.error,
            retryable=(
                run.status.value in ("failed", "stale")
                and run.attempt < run.max_attempts
            ),
            heartbeat_at=run.heartbeat_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
        )


class TaskRunListResponse(BaseModel):
    """任务列表响应"""
    items: List[TaskRunResponse]
    total: int


class CancelResponse(BaseModel):
    """取消响应"""
    task_id: str
    status: str = Field(description="取消后的状态")
    terminated: bool = Field(
        description=(
            "是否已在服务端强制终止。文件 broker 下通常为 false —— "
            "任务会在下一个阶段边界自行退出"
        )
    )
    detail: str


def _require_owner(run: Optional[TaskRun], user: User) -> TaskRun:
    """校验任务归属；不存在或不属于当前用户时一律 404（不泄露存在性）

    Raises:
        AppError 404 TASK_NOT_FOUND: 任务不存在或不属于当前用户
    """
    if run is None or (run.user_id is not None and run.user_id != user.id):
        raise AppError(TASK_NOT_FOUND, "任务不存在", status.HTTP_404_NOT_FOUND)
    return run


@router.get("/{task_id}", response_model=TaskRunResponse)
async def get_task(
    task_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """查询单个任务的进度与状态"""
    run = _require_owner(await task_run_service.get_task_run(db, task_id), current_user)
    return TaskRunResponse.from_model(run)


@router.get("/note/{note_id}", response_model=TaskRunListResponse)
async def list_note_tasks(
    note_id: str,
    limit: int = 20,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """列出某笔记的近期任务（最新在前），供笔记详情页展示处理进度

    仅返回属于当前用户的任务记录。
    """
    limit = max(1, min(limit, 100))
    runs = await task_run_service.list_task_runs_for_note(db, note_id, limit)
    owned = [r for r in runs if r.user_id is None or r.user_id == current_user.id]
    return TaskRunListResponse(
        items=[TaskRunResponse.from_model(r) for r in owned],
        total=len(owned),
    )


@router.post("/{task_id}/cancel", response_model=CancelResponse)
async def cancel_task(
    task_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """请求取消任务

    幂等：已处于终态的任务重复取消返回当前状态而非报错。
    """
    run = _require_owner(await task_run_service.get_task_run(db, task_id), current_user)
    await task_run_service.request_cancel(db, task_id)
    # 重新查询而不是 `db.refresh(run)`：refresh 会用**新事务**重读同一行，
    # 把 request_cancel 里刚提交的赋值回退成库里的旧值
    # （本轮实测：取消后 status 仍显示 running）。
    # 重新 SELECT 则如实取回已提交的新状态。
    updated = await task_run_service.get_task_run(db, task_id)

    return CancelResponse(
        task_id=task_id,
        status=(updated.status.value if updated else run.status.value),
        # 文件 broker 无法可靠强杀执行中的任务，如实告知
        terminated=False,
        detail=(
            "已标记为取消。文件系统 broker 无法强制终止执行中的任务，"
            "任务会在下一个阶段边界自行退出。"
        ),
    )
