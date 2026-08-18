from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


class LinkCreateRequest(BaseModel):
    material_note_ids: List[str] = []


class LinkResponse(BaseModel):
    id: str
    personal_note_id: str
    material_note_id: str
    created_at: datetime
    model_config = {"from_attributes": True}


class LinkListResponse(BaseModel):
    personal_note_id: str
    linked_materials: List[dict] = []  # 资料的简要信息列表
    linked_personal_notes: List[dict] = []  # 反向：引用该资料的笔记列表
    # 悬挂链接数：material_note_id 被物理删除置 NULL 的行数（个人笔记视角），
    # 前端据此显示"[已删除的笔记]"占位行和"清理此链接"入口
    dangling_material_count: int = 0
