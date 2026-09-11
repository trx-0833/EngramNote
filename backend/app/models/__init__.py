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
from .task_run import TaskRun, TaskStatus, ACTIVE_TASK_STATUSES, TERMINAL_TASK_STATUSES
from .review_state import (
    ReviewState,
    ReviewStateKind,
    ITEM_TYPE_CARD,
    ITEM_TYPE_QUIZ,
)
from .chunk import Chunk, pack_vector, unpack_vector

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
    "TaskRun",
    "TaskStatus",
    "ACTIVE_TASK_STATUSES",
    "TERMINAL_TASK_STATUSES",
    "ReviewState",
    "ReviewStateKind",
    "ITEM_TYPE_CARD",
    "ITEM_TYPE_QUIZ",
    "Chunk",
    "pack_vector",
    "unpack_vector",
]
