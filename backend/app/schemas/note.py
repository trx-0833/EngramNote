"""笔记 Pydantic Schema"""

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from ..models.note import NoteRole, NoteStatus, SourceType


# --- 响应模型 ---

class NoteResponse(BaseModel):
    id: str
    user_id: str
    title: str
    source_type: SourceType
    status: NoteStatus
    note_role: str = "material"
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    file_size: int
    page_count: Optional[int] = None
    error_message: Optional[str] = None
    video_url: Optional[str] = None
    linked_material_ids: Optional[List[str]] = None
    linked_personal_note_ids: Optional[List[str]] = None
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


class NoteContentUpdateRequest(BaseModel):
    """笔记内容更新请求"""
    content: str = Field(..., description="Markdown 内容")
    target: Literal["clean", "original"] = Field("clean", description="写入目标：clean 或 original")
