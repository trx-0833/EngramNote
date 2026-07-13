import enum
from typing import Any, Dict, List, Optional

from sqlalchemy import Boolean, DateTime, Float, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


class AssessmentMode(str, enum.Enum):
    compare = "compare"
    quiz = "quiz"
    combined_extract = "combined_extract"


class AssessmentResult(BaseModel):
    __tablename__ = "assessment_results"

    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    material_note_ids: Mapped[Optional[List[str]]] = mapped_column(JSON, default=list)
    personal_note_ids: Mapped[Optional[List[str]]] = mapped_column(JSON, default=list)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    scores: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, default=dict)
    overall_score: Mapped[float] = mapped_column(Float, default=0.0)
    suggestions: Mapped[str] = mapped_column(Text, default="")
    quiz_questions: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(JSON, default=list)
    quiz_answers: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(JSON, default=list)
    # 评估结果关联的笔记-资料配对签名，用于判断结果是否失效（笔记内容变化时签名变化）
    link_signature: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    # 评估结果是否已过期（笔记内容变化后需重新评估），默认 False
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
