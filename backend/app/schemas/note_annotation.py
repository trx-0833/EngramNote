from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


class AnnotationCreateRequest(BaseModel):
    view_mode: str  # original / clean
    type: str  # highlight / underline
    text_content: str
    context_before: str = ""
    context_after: str = ""
    color: Optional[str] = None


class AnnotationResponse(BaseModel):
    id: str
    note_id: str
    view_mode: str
    type: str
    text_content: str
    context_before: str
    context_after: str
    color: Optional[str] = None
    created_at: datetime
    model_config = {"from_attributes": True}


class AnnotationListResponse(BaseModel):
    annotations: List[AnnotationResponse]
