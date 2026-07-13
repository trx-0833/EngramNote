"""
知识卡片和题目 Pydantic Schema

定义 AI 理解管道相关的请求/响应模型。
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from ..models.knowledge_card import CardType, CardCategory
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
    card_category: CardCategory
    is_key_point: bool = False
    is_difficulty: bool = False
    mastery_level: float = 0.0
    source_note_ids: Optional[List[str]] = None
    parent_card_id: Optional[str] = None
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


# --- 联合分析 / 盲点 / 拓展 / 标记 相关 schema ---

class CardMarkRequest(BaseModel):
    """标记重点/难点请求"""
    is_key_point: Optional[bool] = None
    is_difficulty: Optional[bool] = None


class ExtensionGenerateRequest(BaseModel):
    """生成拓展知识点请求"""
    # 可选：指定参考资料上下文的 note_id；不传则仅用父卡片内容
    material_note_id: Optional[str] = None


class CombinedExtractRequest(BaseModel):
    """联合分析请求（可选，主要用 link_id 路径参数）"""
    pass


class CombinedExtractResponse(BaseModel):
    """联合分析响应"""
    link_id: str
    material_note_id: str
    personal_note_id: str
    regular_count: int
    blind_spot_count: int
    total_cards: int
    message: str = ""


class ExtensionGenerateResponse(BaseModel):
    """拓展知识点生成响应"""
    parent_card_id: str
    extension_cards: List[KnowledgeCardResponse] = []
    message: str = ""


class BlindSpotListResponse(BaseModel):
    """盲点列表响应"""
    items: List[KnowledgeCardResponse]
    total: int
    page: int
    page_size: int


class MasteryOverviewItem(BaseModel):
    """掌握度概览单项"""
    card_id: str
    title: str
    card_category: CardCategory
    mastery_level: float
    is_key_point: bool
    is_difficulty: bool
    review_count: int


class MasteryOverviewResponse(BaseModel):
    """掌握度概览响应"""
    items: List[MasteryOverviewItem]
    total: int
    average_mastery: float
