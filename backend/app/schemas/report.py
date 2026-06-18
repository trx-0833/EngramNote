"""
学习报告 Pydantic Schema

定义学习报告相关的响应模型。
"""

from typing import List, Optional

from pydantic import BaseModel


# --- 今日学习报告 ---

class QuestionTypeAccuracy(BaseModel):
    """各题型正确率"""
    question_type: str
    total: int = 0
    correct: int = 0
    accuracy: float = 0.0


class DailyReportResponse(BaseModel):
    """今日学习报告响应"""
    date: str
    new_mastered: int = 0
    total_review_time_ms: int = 0
    total_reviews: int = 0
    today_accuracy: float = 0.0
    weak_point_count: int = 0
    question_type_accuracy: List[QuestionTypeAccuracy] = []


# --- 7天趋势 ---

class WeeklyTrendItem(BaseModel):
    """单日趋势数据"""
    date: str
    review_count: int = 0
    correct_count: int = 0
    accuracy: float = 0.0


class WeeklyTrendResponse(BaseModel):
    """7天趋势响应"""
    items: List[WeeklyTrendItem]
    total_reviews: int = 0
    avg_accuracy: float = 0.0


# --- 薄弱点 ---

class WeakPointItem(BaseModel):
    """薄弱点条目"""
    card_id: str
    card_title: str
    card_type: str
    note_id: str
    note_title: str = ""
    error_count: int = 0
    total_reviews: int = 0
    accuracy: float = 0.0


class WeakPointsResponse(BaseModel):
    """薄弱点列表响应"""
    items: List[WeakPointItem]
    total: int
