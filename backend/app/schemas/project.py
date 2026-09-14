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
    description: Optional[str] = None
    note_count: int = 0
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


# --- 标签维护响应（阶段 5.1：此前这 3 个端点没有 response_model）---


class ProjectNotesAddedResponse(BaseModel):
    """批量给笔记打项目标签的结果

    出处：`project_service.add_notes_to_project` 的 return
    （`projects.py:232` 的端点原样转发）：

        {"project_id": project.id, "added": added, "not_found": len(note_ids) - added}

    ⚠️ `not_found` 的语义是**差集**（请求了 N 个、实际新增 M 个 → N-M），
    它同时包含"笔记不存在/在回收站"与"本来就已经打了这个标签"两种情况 ——
    读这个字段的人会自然以为是前者。**本轮只记录，不改行为**
    （改分母是产品决策，不是类型修复）。
    """

    project_id: str
    added: int
    not_found: int


class ProjectNoteRemovedResponse(BaseModel):
    """把一篇笔记移出项目的结果

    出处：`project_service.remove_note_from_project` 的 return
    （`projects.py:264` 的端点原样转发）：`{"message": ..., "note_id": note_id}`
    """

    message: str
    note_id: str


# --- 请求模型 ---

class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, description="项目名称")
    description: Optional[str] = Field(None, max_length=2000, description="项目描述")


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200, description="项目名称（标签化后名称可改，不影响物理路径）")
    description: Optional[str] = Field(None, max_length=2000, description="项目描述")


class ProjectNotesAddRequest(BaseModel):
    """向项目批量添加笔记的请求（空列表等业务校验由服务层负责）"""
    note_ids: List[str] = Field(default_factory=list, description="要添加到项目的笔记 ID 列表")
