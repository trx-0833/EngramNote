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
