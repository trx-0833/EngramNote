"""
题库模型模块

本模块定义了题库数据表模型，存储根据知识卡片生成的练习题。

主要职责：
- 定义题目类型枚举 QuestionType（选择题、填空题、简答题）
- 定义难度等级枚举 DifficultyLevel（简单、中等、困难）
- 定义题库表结构，关联用户、知识卡片和笔记
- 定义 SM-2 间隔重复调度参数（间隔、重复次数、舒适度因子等）

设计决策：
- 使用枚举类型约束 question_type 和 difficulty，避免无效值
- options 字段存储 JSON 字符串（选择题的选项列表）
- explanation 存储题目解析，辅助学习理解
- SM-2 参数（interval, repetition, easiness_factor, next_review_at）存储在题目表上，
  避免额外的 JOIN 查询
- next_review_at 默认为题目创建时间，即新题目立即可复习
- metadata_ 字段使用 JSON 类型，映射到数据库列名为 metadata
"""

import enum
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import Enum, String, Text, JSON, Integer, Float, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel, TZDateTime


class QuestionType(str, enum.Enum):
    """
    题目类型枚举

    标识题目的形式，决定答题和判分方式：
    - choice: 选择题，4个选项1个正确答案
    - fill_blank: 填空题，关键概念留空
    - short_answer: 简答题，要求简明扼要回答
    """
    choice = "choice"
    fill_blank = "fill_blank"
    short_answer = "short_answer"


class DifficultyLevel(str, enum.Enum):
    """
    难度等级枚举

    标识题目的难度，用于间隔重复算法调整复习频率：
    - easy: 简单，基础概念回忆
    - medium: 中等，需要理解和应用
    - hard: 困难，需要综合分析和推理
    """
    easy = "easy"
    medium = "medium"
    hard = "hard"


class QuizItem(BaseModel):
    """
    题库模型

    对应数据库中的 quiz_items 表。
    每条记录代表根据知识卡片生成的一道练习题。

    Attributes:
        id: UUID 主键（继承自 BaseModel）
        user_id: 所属用户 ID，外键关联 users 表
        card_id: 关联知识卡片 ID，外键关联 knowledge_cards 表
        note_id: 来源笔记 ID，外键关联 notes 表
        question_type: 题目类型（选择题/填空题/简答题）
        difficulty: 难度等级（简单/中等/困难）
        question: 题目内容
        answer: 正确答案
        options: 选择题选项（JSON 字符串），仅选择题有值
        explanation: 题目解析
        metadata_: 扩展元数据（JSON 格式）
        created_at: 创建时间（继承自 BaseModel）
        updated_at: 更新时间（继承自 BaseModel）
    """
    __tablename__ = "quiz_items"

    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True, nullable=False)
    card_id: Mapped[str] = mapped_column(String, ForeignKey("knowledge_cards.id"), index=True, nullable=False)
    # 来源笔记 ID；物理删除笔记时置 NULL（题目随卡片存亡，提升卡片时悬挂保留）
    note_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("notes.id", ondelete="SET NULL"), index=True, nullable=True
    )
    question_type: Mapped[QuestionType] = mapped_column(Enum(QuestionType), nullable=False)
    difficulty: Mapped[DifficultyLevel] = mapped_column(
        Enum(DifficultyLevel), default=DifficultyLevel.medium, nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    explanation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_: Mapped[Optional[Dict[str, Any]]] = mapped_column("metadata", JSON, nullable=True)

    # ---- SM-2 间隔重复调度参数 ----
    # 复习间隔（天），首次为1天
    interval: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # 连续正确回忆次数，回忆失败时重置为0
    repetition: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 舒适度因子（最小1.3），反映题目对用户的难易程度
    easiness_factor: Mapped[float] = mapped_column(Float, default=2.5, nullable=False)
    # 下次复习时间，默认为创建时间（新题目立即可复习）
    next_review_at: Mapped[Optional[datetime]] = mapped_column(
        TZDateTime(timezone=True), nullable=True, default=None, index=True
    )
    # 上次复习时间
    last_reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        TZDateTime(timezone=True), nullable=True, default=None
    )
    # 累计复习次数
    review_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
