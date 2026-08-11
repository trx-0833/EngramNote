"""项目 Pydantic Schema"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# --- 响应模型 ---

class NoteSummary(BaseModel):
    """项目详情中笔记的简要信息"""
    id: str
    title: str
    status: str
    source_type: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ProjectResponse(BaseModel):
    id: str
    user_id: str
    name: str
    slug: str
    description: Optional[str] = None
    note_count: int = 0
    vault_path: str = ""
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectDetailResponse(ProjectResponse):
    """项目详情 — 包含项目下的笔记列表"""
    notes: List[NoteSummary] = []


class ScanImportDetail(BaseModel):
    """扫描导入的单条新笔记信息"""
    id: str
    title: str
    status: str
    source_type: Optional[str] = None
    path: str


class ScanSkipDetail(BaseModel):
    """被跳过的文件信息"""
    path: str
    reason: str


class ScanImportResponse(BaseModel):
    """扫描 source/ 目录并导入新文件的响应"""
    project_id: str
    project_name: str
    scanned: int
    imported: int
    skipped: int
    unsupported: int
    imported_notes: List[ScanImportDetail] = []
    skipped_details: List[ScanSkipDetail] = []
    unsupported_details: List[ScanSkipDetail] = []


# --- 请求模型 ---

class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, description="项目名称")
    description: Optional[str] = Field(None, max_length=2000, description="项目描述")


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200, description="项目名称（slug 不可变）")
    description: Optional[str] = Field(None, max_length=2000, description="项目描述")


class ProjectNotesAddRequest(BaseModel):
    """向项目批量添加笔记的请求（空列表等业务校验由服务层负责）"""
    note_ids: List[str] = Field(default_factory=list, description="要添加到项目的笔记 ID 列表")
