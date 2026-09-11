"""
任务运行记录模型模块

本模块定义 task_runs 表，为每个 Celery 任务留下一条**可被 API 查询**的
生命周期记录，用于两件事：

1. **进度可见**（阶段 1′ 1.7）：此前任务只有笔记上的状态枚举
   （uploading → converting → …），UI 只能转圈，无法显示百分比、
   当前阶段名，也无法取消。
2. **僵尸任务自愈**（阶段 1′ 1.8）：文件系统 broker 没有 visibility
   timeout，worker 崩溃后任务永久停在"未确认"状态，而笔记则永久卡在
   converting。靠 heartbeat_at 超时把这类任务判定为僵尸并标记失败，
   用户才能重试。

设计决策：
- task_id 唯一：同一 Celery 任务只应有一条记录，重试时复用同一行并
  递增 attempt（而不是新增行，否则"这张笔记现在在做什么"会有多行答案）
- progress 用 0.0~1.0 的浮点而非百分比整数：避免"99% 卡死"这类
  无法区分"快好了"和"卡住了"的表示
- heartbeat_at 与 updated_at 分开：updated_at 会因任何字段更新而变，
  而心跳的语义是"worker 还活着"，必须能被显式刷新而不误改业务字段
- error 只存截断后的摘要（见 _ERROR_MAX_LEN）：完整堆栈进日志文件，
  不写进数据库（避免把用户内容或密钥带进可被 API 读取的表）
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel, TZDateTime

# 错误摘要写入数据库的最大长度；更长的部分只进日志
ERROR_MAX_LEN = 2000


class TaskStatus(str, Enum):
    """
    任务运行状态

    - pending:   已入队但 worker 尚未接手
    - running:   已被 worker 取走并执行中
    - succeeded: 成功结束
    - failed:    失败结束（含超过重试上限）
    - stale:     心跳超时判定为僵尸（worker 崩溃或任务被遗弃）
    - cancelled: 用户主动取消
    """
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    stale = "stale"
    cancelled = "cancelled"


#: 视为"仍在进行中"的状态集合（僵尸扫描只看这些）
ACTIVE_TASK_STATUSES = (TaskStatus.pending, TaskStatus.running)

#: 终态集合（不可再变；写入前需确认）
TERMINAL_TASK_STATUSES = (
    TaskStatus.succeeded, TaskStatus.failed,
    TaskStatus.stale, TaskStatus.cancelled,
)


class TaskRun(BaseModel):
    """
    任务运行记录

    Attributes:
        task_id: Celery 任务 ID（唯一）
        task_name: 任务全名，如 app.tasks.convert_tasks.convert_note
        note_id: 关联笔记（可为空，非笔记任务没有）
        user_id: 触发用户（可为空，Beat 定时任务没有）
        status: 生命周期状态
        progress: 完成度 0.0~1.0
        stage: 当前阶段的可读名称（直接展示给用户，如"正在解析 PDF"）
        message: 附加说明（可为空）
        attempt: 第几次尝试（从 1 开始，重试时递增）
        max_attempts: 允许的最大尝试次数
        error: 失败摘要（截断至 ERROR_MAX_LEN）
        heartbeat_at: 最近一次心跳时间（僵尸判定依据）
        started_at / finished_at: 起止时间
    """
    __tablename__ = "task_runs"

    task_id: Mapped[str] = mapped_column(String(155), nullable=False, unique=True, index=True)
    task_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    note_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    user_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)

    status: Mapped[TaskStatus] = mapped_column(
        SAEnum(TaskStatus), nullable=False, default=TaskStatus.pending, index=True,
    )
    progress: Mapped[float] = mapped_column(nullable=False, default=0.0)
    stage: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(
        TZDateTime(timezone=True), nullable=True,
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        TZDateTime(timezone=True), nullable=True,
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        TZDateTime(timezone=True), nullable=True,
    )

    __table_args__ = (
        # 僵尸扫描的查询形状：WHERE status IN (...) AND heartbeat_at < cutoff
        Index("ix_task_runs_status_heartbeat", "status", "heartbeat_at"),
        # 笔记详情页要按笔记倒序列出该笔记的全部任务
        Index("ix_task_runs_note_created", "note_id", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"<TaskRun {self.task_name}#{self.task_id[:8]} "
            f"{self.status.value} {self.progress:.0%}>"
        )
