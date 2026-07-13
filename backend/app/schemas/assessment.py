from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime


class CompareRequest(BaseModel):
    material_note_ids: List[str]
    personal_note_ids: List[str]


class QuizGenerateRequest(BaseModel):
    material_note_ids: List[str]
    personal_note_id: Optional[str] = None


class AnswerSubmitRequest(BaseModel):
    assessment_id: str
    answers: List[Dict[str, Any]]  # [{question_index: int, answer: str}]


class AssessmentResponse(BaseModel):
    id: str
    mode: str
    scores: Dict[str, Any]
    overall_score: float
    suggestions: str
    quiz_questions: Optional[List[Dict[str, Any]]] = None
    quiz_answers: Optional[List[Dict[str, Any]]] = None
    link_signature: Optional[str] = None
    is_stale: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class AssessmentHistoryItem(BaseModel):
    id: str
    mode: str
    overall_score: float
    created_at: datetime

    model_config = {"from_attributes": True}
