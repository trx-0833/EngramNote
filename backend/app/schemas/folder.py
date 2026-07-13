"""文件夹 Pydantic Schema"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from ..models.note import NoteStatus, SourceType


# --- 请求模型 ---

class FolderCreate(BaseModel):
    """文件夹创建请求"""
    name: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = None
    # ISO 日期字符串，如 "2024-01-15"，默认为今天
    folder_date: Optional[str] = None


class FolderUpdate(BaseModel):
    """文件夹更新请求（用于重命名）"""
    name: Optional[str] = Field(None, min_length=1, max_length=500)


# --- 响应模型 ---

class NoteInFolder(BaseModel):
    """文件夹内的笔记概要信息"""
    id: str
    title: str
    source_type: SourceType
    status: NoteStatus
    file_size: int
    created_at: datetime

    model_config = {"from_attributes": True}


class FolderResponse(BaseModel):
    """文件夹响应"""
    id: str
    user_id: str
    name: str
    description: Optional[str] = None
    folder_date: datetime
    created_at: datetime
    note_count: int = 0

    model_config = {"from_attributes": True}


class FolderDetailResponse(FolderResponse):
    """文件夹详情响应 — 包含笔记列表"""
    notes: List[NoteInFolder] = []
