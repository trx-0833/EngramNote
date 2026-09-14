from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from ..models.note import SourceType


class LinkCreateRequest(BaseModel):
    material_note_ids: List[str] = []


class LinkResponse(BaseModel):
    id: str
    personal_note_id: str
    material_note_id: str
    created_at: datetime
    model_config = {"from_attributes": True}


class LinkedMaterialItem(BaseModel):
    """被个人笔记引用的资料条目

    出处：`api/notes/links.py:46-53`（`get_note_links` 里列表推导的字典）：

        {"id": m.id, "title": m.title,
         "source_type": m.source_type.value if m.source_type else None}

    ⚠️ `source_type` **确实可空**：`Note.source_type` 在库里是 nullable，
    该分支显式写了 `if m.source_type else None`。前端
    `frontend/src/api/notes.ts` 的 `LinkedMaterial` 正好也写的是
    `source_type: string | null` —— 两边一致，这是少见的"前端比后端契约更准"的一处
    （此前 schema 里这里是裸 `dict`，所以前端只能靠手写维持正确）。
    """

    id: str
    title: str
    source_type: Optional[SourceType] = None


class LinkedPersonalNoteItem(BaseModel):
    """引用了该资料的个人笔记条目

    出处：`api/notes/links.py:68`：`[{"id": n.id, "title": n.title} for n in personal_notes]`
    """

    id: str
    title: str


class LinkListResponse(BaseModel):
    personal_note_id: str
    # 资料的简要信息列表
    linked_materials: List[LinkedMaterialItem] = []
    # 反向：引用该资料的笔记列表
    linked_personal_notes: List[LinkedPersonalNoteItem] = []
    # 悬挂链接数：material_note_id 被物理删除置 NULL 的行数（个人笔记视角），
    # 前端据此显示"[已删除的笔记]"占位行和"清理此链接"入口
    dangling_material_count: int = 0


class LinkUpdateResponse(BaseModel):
    """更新笔记-资料链接的结果

    出处：`api/notes/links.py:123`：`return {"changed": changed}`

    `changed` 来自 `note_service.update_note_material_links` 的布尔返回值：
    True 表示链接集合真的变了（同时会顺带把相关 quiz 缓存标记为 stale）。
    前端 `frontend/src/api/notes.ts` 的 `UpdateLinksResponse` 与之一致。
    """

    changed: bool
