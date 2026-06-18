"""
复习模块 Pydantic Schema

定义复习调度相关的请求/响应模型。
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

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
