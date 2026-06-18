"""笔记清洗 Pydantic Schema"""

from typing import Dict, List, Optional

from pydantic import BaseModel

from ..models.note import NoteStatus


# --- 响应模型 ---

class CleaningStartResponse(BaseModel):
    """触发清洗的响应"""
    id: str
    status: NoteStatus
    message: str

    model_config = {"from_attributes": True}


class CleaningStatusResponse(BaseModel):
    """清洗状态查询响应"""
    id: str
    status: NoteStatus
    clean_md_path: Optional[str] = None
    error_message: Optional[str] = None
    metadata_: Optional[Dict] = None

    model_config = {"from_attributes": True}


class DiffLine(BaseModel):
    """单行 diff 数据"""
    type: str  # "added", "removed", "unchanged"
    content: str
    line_number_original: Optional[int] = None
    line_number_clean: Optional[int] = None


class DiffBlock(BaseModel):
    """diff 块数据（连续的变更行）"""
    lines: List[DiffLine]


class CleaningDiffResponse(BaseModel):
    """清洗 diff 数据响应"""
    note_id: str
    original_lines: int
    clean_lines: int
    blocks: List[DiffBlock]
    stats: Optional[Dict] = None


class BlockOperationResponse(BaseModel):
    """块操作（恢复/删除）响应"""
    note_id: str
    block_index: int
    operation: str  # "restored" or "deleted"
    message: str


class CleaningStopResponse(BaseModel):
    """停止清洗响应"""
    id: str
    status: NoteStatus
    message: str
