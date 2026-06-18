"""
知识卡片和题目 Pydantic Schema

定义 AI 理解管道相关的请求/响应模型。
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from ..models.knowledge_card import CardType
from ..models.quiz_item import QuestionType, DifficultyLevel
from ..models.note import NoteStatus


# --- 知识卡片响应模型 ---

class KnowledgeCardResponse(BaseModel):
    """知识卡片响应"""
    id: str
    user_id: str
    note_id: str
    note_title: str = ""
    card_type: CardType
    title: str
    content: str
    summary: Optional[str] = None
    chapter_title: Optional[str] = None
    source_text: Optional[str] = None
    metadata_: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeCardListResponse(BaseModel):
    """知识卡片列表响应"""
    items: List[KnowledgeCardResponse]
    total: int
    page: int
    page_size: int


class CardUpdateRequest(BaseModel):
    """卡片更新请求"""
    title: Optional[str] = None
    content: Optional[str] = None


# --- 题目响应模型 ---

class QuizItemResponse(BaseModel):
    """题目响应"""
    id: str
    user_id: str
    card_id: str
    note_id: str
    note_title: str = ""
    question_type: QuestionType
    difficulty: DifficultyLevel
    question: str
    answer: str
    options: Optional[str] = None
    explanation: Optional[str] = None
    metadata_: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class QuizItemListResponse(BaseModel):
    """题目列表响应"""
    items: List[QuizItemResponse]
    total: int
    page: int
    page_size: int


# --- 理解管道响应模型 ---

class UnderstandingStartResponse(BaseModel):
    """触发理解管道的响应"""
    id: str
    status: NoteStatus
    message: str

    model_config = {"from_attributes": True}


class UnderstandingStatusResponse(BaseModel):
    """理解管道状态查询响应"""
    id: str
    status: NoteStatus
    error_message: Optional[str] = None

    model_config = {"from_attributes": True}


class ChapterSummary(BaseModel):
    """章节摘要"""
    chapter_index: int
    chapter_title: str
    summary: str
    card_count: int = 0


class ChapterSummaryListResponse(BaseModel):
    """章节摘要列表响应"""
    note_id: str
    chapters: List[ChapterSummary]


# --- 问答请求/响应模型 ---

class QuestionRequest(BaseModel):
    """问答请求"""
    question: str


class AnswerSource(BaseModel):
    """回答引用来源"""
    note_id: str
    note_title: str
    chapter_title: Optional[str] = None
    relevant_text: str


class QuestionAnswerResponse(BaseModel):
    """问答响应"""
    question: str
    answer: str
    sources: List[AnswerSource] = []
    provider: str = ""


# --- 题目生成响应模型 ---

class GenerateQuestionsResponse(BaseModel):
    """触发题目生成的响应"""
    note_id: str
    message: str
    question_count: int = 0
