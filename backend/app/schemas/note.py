"""笔记 Pydantic Schema"""

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from ..models.note import NoteStatus, SourceType


# --- 响应模型 ---

class NoteResponse(BaseModel):
    id: str
    user_id: str
    title: str
    source_type: SourceType
    status: NoteStatus
    file_size: int
    page_count: Optional[int] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class NoteDetailResponse(NoteResponse):
    """笔记详情 — 包含 Markdown 内容"""
    original_file_path: Optional[str] = None
    original_md_path: Optional[str] = None
    original_md_content: Optional[str] = None
    clean_md_content: Optional[str] = None
    metadata_: Optional[Dict] = None


class NoteListResponse(BaseModel):
    items: List[NoteResponse]
    total: int
    page: int
    page_size: int


# --- 请求模型 ---

class NoteUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=500)


class NoteStatusResponse(BaseModel):
    id: str
    status: NoteStatus
    error_message: Optional[str] = None
