"""笔记 Pydantic Schema"""

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from ..models.note import NoteStatus, SourceType


# --- 响应模型 ---

class NoteResponse(BaseModel):
    id: str
    user_id: str
    title: str
    source_type: SourceType
    status: NoteStatus
    note_role: str = "material"
    project_ids: List[str] = []
    project_names: List[str] = []
    file_size: int
    page_count: Optional[int] = None
    error_message: Optional[str] = None
    video_url: Optional[str] = None
    linked_material_ids: Optional[List[str]] = None
    linked_personal_note_ids: Optional[List[str]] = None
    trashed_at: Optional[datetime] = None
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
    #: 这篇笔记**还没补向量**的 chunk 数（B2 的"可见"那半，2026-10-22）
    #:
    #: ## 为什么要有它
    #:
    #: 清洗路径刻意不写向量（要加载 4.3GB 模型，不能放在高并发的用户路径里），
    #: 补嵌入由 Beat 的 `backfill_embeddings` 周期补齐（B2 已落地）。
    #: 但在它补上之前，这篇笔记**只被 BM25 检索到** —— 换个说法/同义的提问
    #: 会答不出或答歪，而**用户界面上完全看不出来**。
    #:
    #: 此前只有两处能看到：`/ready` 的 `index.pending_embeddings`（运维探针）
    #: 与问答时的 SSE `meta.retrieval_status`（要先提一个问题才会出现）。
    #: 这个字段让**不提问也看得到** —— 那是 `docs/overhaul-plan.md` 附录 BN.3
    #: 把它列为「确实影响使用」时缺的那一环。
    #:
    #: `0` = 全部已建索引（正常）；`None` = 还没统计（如笔记还没清洗完）——
    #: 与 `pending_embeddings` 同一条原则：**"没测到"不能长得像"没有问题"**。
    pending_embedding_chunks: Optional[int] = None


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


# --- 回收站（Trash）响应模型 ---

class TrashNoteItem(BaseModel):
    """回收站列表项：笔记 + 附属统计（"恢复可还原什么"的展示依据）"""
    note: NoteResponse
    card_count: int = 0
    quiz_count: int = 0
    annotation_count: int = 0
    version_count: int = 0
    link_count: int = 0


class TrashListResponse(BaseModel):
    """回收站列表响应"""
    items: List[TrashNoteItem]
    total: int


class TrashInfoResponse(BaseModel):
    """删除确认弹窗的关联统计"""
    card_count: int = 0
    key_card_count: int = 0
    link_count: int = 0


class RestoreResponse(BaseModel):
    """恢复结果：恢复后的笔记 + 同名冲突改名提示（无冲突为 None）"""
    note: NoteResponse
    renamed_to: Optional[str] = None


class PurgeAllResponse(BaseModel):
    """清空回收站结果"""
    purged: int
    failed: int
