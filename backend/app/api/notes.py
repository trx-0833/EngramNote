"""
笔记 CRUD API 模块

本模块提供笔记的增删改查 HTTP 接口，所有接口均需要用户认证。
笔记是系统的核心资源，用户只能访问自己创建的笔记。

主要职责：
- 获取笔记列表（GET /api/notes），支持分页和关键词搜索
- 获取笔记详情（GET /api/notes/{note_id}），包含 Markdown 内容
- 更新笔记（PUT /api/notes/{note_id}），目前仅支持修改标题
- 删除笔记（DELETE /api/notes/{note_id}），同时删除关联的存储文件

设计决策：
- 所有接口通过 get_current_user_dependency 确保用户已认证
- 笔记查询自动过滤 user_id，确保用户只能访问自己的数据
- 获取详情时同时读取原始和清洗后的 Markdown 内容
- 删除笔记时同步清理对象存储中的关联文件
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.note import Note, NoteStatus
from ..models.user import User
from ..schemas.note import (
    NoteDetailResponse,
    NoteListResponse,
    NoteResponse,
    NoteUpdateRequest,
)
from ..api.auth import get_current_user_dependency
from ..services.note_service import (
    delete_note,
    get_clean_markdown_content,
    get_note_detail,
    get_note_markdown_content,
    get_notes_list,
    update_note,
)

router = APIRouter()


@router.get("", response_model=NoteListResponse)
async def list_notes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: Optional[str] = None,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取笔记列表

    返回当前用户的笔记列表，支持分页和标题关键词搜索。
    按创建时间倒序排列（最新笔记在前）。

    Args:
        page: 页码，从 1 开始，默认第 1 页
        page_size: 每页数量，默认 20，最大 100
        keyword: 搜索关键词，按标题模糊匹配（可选）
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        NoteListResponse: 包含笔记列表、总数、分页信息的响应
    """
    notes, total = await get_notes_list(db, current_user.id, page, page_size, keyword)
    return NoteListResponse(
        items=[NoteResponse.model_validate(n) for n in notes],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/archive", response_model=NoteListResponse)
async def list_archived_notes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取已归档笔记列表

    按创建时间倒序排列，专门展示已归档的笔记。
    """
    notes, total = await get_notes_list(
        db, current_user.id, page, page_size, note_status=NoteStatus.archived
    )
    return NoteListResponse(
        items=[NoteResponse.model_validate(n) for n in notes],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{note_id}", response_model=NoteDetailResponse)
async def get_note(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取笔记详情

    返回笔记的基本信息和 Markdown 内容（包括原始转换结果和清洗后结果）。
    Markdown 内容从对象存储中实时读取，不存储在数据库中。

    Args:
        note_id: 笔记 ID
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        NoteDetailResponse: 包含笔记详情和 Markdown 内容的响应

    Raises:
        HTTPException 404: 笔记不存在或不属于当前用户
    """
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    # 从对象存储中读取 Markdown 内容
    original_md = await get_note_markdown_content(note)
    clean_md = await get_clean_markdown_content(note)

    resp = NoteDetailResponse.model_validate(note)
    resp.original_md_content = original_md
    resp.clean_md_content = clean_md
    return resp


@router.put("/{note_id}", response_model=NoteResponse)
async def update_note_api(
    note_id: str,
    req: NoteUpdateRequest,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    更新笔记

    目前仅支持修改笔记标题。后续可扩展支持更多字段的更新。

    Args:
        note_id: 笔记 ID
        req: 更新请求体，包含需要修改的字段
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        NoteResponse: 更新后的笔记信息

    Raises:
        HTTPException 404: 笔记不存在或不属于当前用户
    """
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    updated = await update_note(db, note, req)
    return NoteResponse.model_validate(updated)


@router.post("/{note_id}/archive", response_model=NoteResponse)
async def archive_note_api(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    手动归档/取消归档笔记

    切换笔记的归档状态：
    - archived → cleaned（取消归档）
    - cleaned/learning_failed → archived（归档）
    仅 converted/cleaned/learning_failed/archived 状态可操作。

    Args:
        note_id: 笔记 ID
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        NoteResponse: 更新后的笔记信息
    """
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    if note.status not in (
        NoteStatus.archived, NoteStatus.cleaned,
        NoteStatus.learning_failed, NoteStatus.converted,
    ):
        raise HTTPException(
            status_code=400,
            detail=f"当前状态 {note.status.value} 不允许归档/取消归档操作",
        )

    if note.status == NoteStatus.archived:
        note.status = NoteStatus.cleaned
    else:
        note.status = NoteStatus.archived
    note.error_message = None
    await db.commit()
    await db.refresh(note)
    return NoteResponse.model_validate(note)


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note_api(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    删除笔记

    删除笔记记录及其在对象存储中的所有关联文件（原始文件、原始 Markdown、清洗后 Markdown）。
    删除操作不可逆。

    Args:
        note_id: 笔记 ID
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        无返回内容（204 No Content）

    Raises:
        HTTPException 404: 笔记不存在或不属于当前用户
    """
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    # 删除笔记记录及关联的存储文件
    await delete_note(db, note)
