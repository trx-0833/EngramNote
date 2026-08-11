"""
复习模块 Pydantic Schema

定义复习调度相关的请求/响应模型。
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..models.quiz_item import QuestionType, DifficultyLevel


# --- 到期题目响应模型 ---

class DueQuizResponse(BaseModel):
    """到期题目响应"""
    id: str
    card_id: str
    note_id: str
    question_type: QuestionType
    difficulty: DifficultyLevel
    question: str
    options: Optional[str] = None
    next_review_at: Optional[datetime] = None
    review_count: int = 0
    interval: int = 1
    repetition: int = 0
    easiness_factor: float = 2.5

    model_config = {"from_attributes": True}


class DueQuizListResponse(BaseModel):
    """到期题目列表响应"""
    items: List[DueQuizResponse]
    total: int


# --- 提交答案请求/响应模型 ---

class SubmitAnswerRequest(BaseModel):
    """提交答案请求"""
    quiz_id: str
    user_answer: str
    time_spent_ms: int = 0


class SM2Info(BaseModel):
    """SM-2 算法更新信息"""
    interval: int
    repetition: int
    easiness_factor: float
    next_review_at: str


class SubmitAnswerResponse(BaseModel):
    """提交答案响应"""
    quiz_id: str
    is_correct: bool
    quality: int
    correct_answer: str
    explanation: Optional[str] = None
    options: Optional[List[str]] = None
    question_type: str
    sm2: SM2Info


# --- 复习统计响应模型 ---

class ReviewStatsResponse(BaseModel):
    """复习统计响应"""
    due_count: int = 0
    today_done: int = 0
    today_correct: int = 0
    today_accuracy: float = 0.0
    total_reviews: int = 0
    total_correct: int = 0
    total_accuracy: float = 0.0
    total_quizzes: int = 0


# --- 复习历史响应模型 ---

class ReviewHistoryItem(BaseModel):
    """复习历史条目"""
    id: str
    quiz_id: str
    note_id: str
    user_answer: str
    is_correct: bool
    quality: int
    time_spent_ms: int
    review_at: Optional[str] = None


class ReviewHistoryResponse(BaseModel):
    """复习历史响应"""
    items: List[ReviewHistoryItem]
    total: int
    page: int
    page_size: int


# --- 复习提醒响应模型 ---

class ReminderResponse(BaseModel):
    """
    复习提醒响应

    返回当前用户的复习提醒概览数据，包括到期题目数、
    1 小时内到期题目数、薄弱知识点数和上次提醒时间。

    字段与 notification_service.NotificationService.get_reminders() 返回的字典键一致。
    """
    due_count: int = Field(description="当前到期需要复习的题目数")
    due_in_1h_count: int = Field(description="1小时内到期的题目数")
    weak_point_count: int = Field(description="薄弱知识点数（mastery_level < 60）")
    last_reminded_at: Optional[datetime] = Field(
        default=None, description="上次提醒时间"
    )

    model_config = ConfigDict(from_attributes=True)
