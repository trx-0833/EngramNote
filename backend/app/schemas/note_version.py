"""笔记版本历史 Pydantic Schema"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# --- 响应模型 ---


class NoteVersionResponse(BaseModel):
    """单个版本快照信息"""
    id: str
    note_id: str
    version_number: int
    source: str
    content_size: int
    change_summary: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class NoteVersionListResponse(BaseModel):
    """版本历史列表响应"""
    versions: List[NoteVersionResponse]
    total: int


class NoteVersionDiffLine(BaseModel):
    """单行 diff 数据"""
    type: str = Field(..., description="行类型：added / removed / unchanged")
    content: str


class NoteVersionDiffResponse(BaseModel):
    """两个版本对比的 diff 响应"""
    v1_number: int
    v2_number: int
    diff_lines: List[NoteVersionDiffLine]


class NoteVersionContentResponse(BaseModel):
    """单个版本快照的 Markdown 内容（预览用）

    出处：`api/notes/versions.py:147`：

        return {"content": content, "version_number": version_number}

    `version_number` 就是**请求路径里那个**版本号（不是从内容里解析出来的），
    回显它是为了让调用方不必把路径参数与响应配起来读 —— 前端
    `frontend/src/api/notes.ts` 的 `getVersion` 也正按 `{content, version_number}` 解析。
    """

    content: str
    version_number: int


# --- 请求模型 ---


class NoteVersionRestoreRequest(BaseModel):
    """恢复历史版本请求"""
    confirm: Optional[bool] = Field(
        None, description="确认恢复标志，预留用于前端二次确认"
    )
