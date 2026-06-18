"""模型注册 — 导入所有模型以便 Alembic 发现"""

from .user import User
from .note import Note, SourceType, NoteStatus
from .knowledge_card import KnowledgeCard, CardType
from .quiz_item import QuizItem, QuestionType, DifficultyLevel
from .review_log import ReviewLog

__all__ = [
    "User",
    "Note",
    "SourceType",
    "NoteStatus",
    "KnowledgeCard",
    "CardType",
    "QuizItem",
    "QuestionType",
    "DifficultyLevel",
    "ReviewLog",
]
