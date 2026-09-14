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


class AnnotationDeleteResponse(BaseModel):
    """删除批注的结果

    出处：`api/notes/links.py:183`：`return {"success": True}`

    ⚠️ 前端 `frontend/src/api/notes.ts` 的 `deleteAnnotation` 把它标成
    `Promise<void>`（连返回值都不看），实际后端一直返回 `{"success": true}`。
    这是"前端比现实更窄"（丢掉了一个恒为 true 的字段），**不是 bug**，
    但切到生成类型后前端会看到它 —— 属于收益而非风险。
    """

    success: bool
