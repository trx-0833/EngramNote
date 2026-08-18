"""
复习记录模型模块

本模块定义了复习记录数据表模型，记录用户每次答题的详细信息，
是间隔重复算法的数据基础。

主要职责：
- 定义复习记录表结构，关联用户、题目和笔记
- 记录用户答案、正误判断、SM-2 评分和答题耗时

设计决策：
- note_id 冗余存储，方便按笔记维度查询复习记录
- quality 字段存储 SM-2 算法的 0-5 评分，便于后续分析
- time_spent_ms 记录答题耗时，可用于薄弱点分析
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, Float, Boolean, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel, TZDateTime


class ReviewLog(BaseModel):
    """
    复习记录模型

    对应数据库中的 review_logs 表。
    每条记录代表用户对一道题的一次作答。

    Attributes:
        id: UUID 主键（继承自 BaseModel）
        user_id: 所属用户 ID，外键关联 users 表
        quiz_id: 关联题目 ID，外键关联 quiz_items 表
        note_id: 来源笔记 ID，外键关联 notes 表（冗余，方便查询）
        user_answer: 用户提交的答案
        is_correct: 是否正确
        quality: SM-2 评分 (0-5)
        time_spent_ms: 答题耗时（毫秒）
        review_at: 答题时间
        created_at: 创建时间（继承自 BaseModel）
        updated_at: 更新时间（继承自 BaseModel）
    """
    __tablename__ = "review_logs"

    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True, nullable=False)
    quiz_id: Mapped[str] = mapped_column(String, ForeignKey("quiz_items.id"), index=True, nullable=False)
    # 来源笔记 ID（冗余）；物理删除笔记时置 NULL（悬挂保留），复习历史不因笔记删除而丢失
    note_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("notes.id", ondelete="SET NULL"), index=True, nullable=True
    )
    user_answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quality: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    time_spent_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    review_at: Mapped[datetime] = mapped_column(TZDateTime(timezone=True), nullable=False, index=True)
