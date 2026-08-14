"""模型注册 — 导入所有模型以便 Alembic 发现"""

from .user import User
from .note import Note, SourceType, NoteStatus
from .folder import Folder
from .project import Project
from .knowledge_card import KnowledgeCard, CardType, CardCategory
from .quiz_item import QuizItem, QuestionType, DifficultyLevel
from .review_log import ReviewLog
from .card_relation import CardRelation, RelationType, RelationStatus
from .assessment import AssessmentResult, AssessmentMode
from .note_material_link import NoteMaterialLink
from .note_annotation import NoteAnnotation
from .learning_goal import LearningGoal, DailyPlan, GoalType, GoalStatus
from .note_version import NoteVersion, VersionSource
from .note_project import NoteProject

__all__ = [
    "User",
    "Note",
    "SourceType",
    "NoteStatus",
    "Folder",
    "Project",
    "KnowledgeCard",
    "CardType",
    "CardCategory",
    "QuizItem",
    "QuestionType",
    "DifficultyLevel",
    "ReviewLog",
    "CardRelation",
    "RelationType",
    "RelationStatus",
    "AssessmentResult",
    "AssessmentMode",
    "NoteMaterialLink",
    "NoteAnnotation",
    "LearningGoal",
    "DailyPlan",
    "GoalType",
    "GoalStatus",
    "NoteVersion",
    "VersionSource",
    "NoteProject",
]
