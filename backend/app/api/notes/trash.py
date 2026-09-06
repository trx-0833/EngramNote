"""
笔记本回收站 API（子路由模块）

原 api/notes.py 拆分出的回收站部分：移入回收站、恢复、彻底删除与清空。
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_db
from ...models.user import User
from ...schemas.note import (
    PurgeAllResponse,
    RestoreResponse,
    TrashInfoResponse,
    TrashListResponse,
    TrashNoteItem,
)
from ...api.auth import get_current_user_dependency
from ...services.note_service import (
    get_note_detail,
    get_trash_info,
    get_trashed_notes,
    purge_all_trashed,
    purge_note,
    restore_note,
    trash_note,
)

from ._common import _build_note_response

router = APIRouter()


@router.get("/trash", response_model=TrashListResponse)
async def list_trashed_notes(
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取回收站笔记列表（含附属统计）

    每项含卡片数、题目数、批注数、版本数、双向链接数，作为
    "恢复可还原什么"的展示依据。按移入时间倒序排列。

    注意：本路由为字面量路径，必须注册在 GET /{note_id} 之前，
    否则 "trash" 会被当作 note_id 匹配。
    """
    items_data = await get_trashed_notes(db, current_user.id)
    items = [
        TrashNoteItem(
            note=await _build_note_response(db, item["note"]),
            card_count=item["card_count"],
            quiz_count=item["quiz_count"],
            annotation_count=item["annotation_count"],
            version_count=item["version_count"],
            link_count=item["link_count"],
        )
        for item in items_data
    ]
    return TrashListResponse(items=items, total=len(items))


@router.delete("/trash/purge-all", response_model=PurgeAllResponse)
async def purge_all_trash_api(
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    清空回收站：物理删除回收站中的所有笔记（悬挂引用策略）

    关系记录（CardRelation / NoteMaterialLink）被删端置 NULL 悬挂保留，
    绝不级联删除；不提升核心卡片。
    """
    result = await purge_all_trashed(db, current_user.id)
    return PurgeAllResponse(**result)


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note_api(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    移入回收站（软删除）

    笔记及其全部子内容（卡片/题目/复习记录/版本/批注/关系/双链）作为
    原子包整体进回收站：仅标记 trashed_at，所有关系记录原地不动；
    物理文件搬至 {user_id}/trash/{note_id}/ 隔离目录。
    可随时通过回收站恢复或彻底删除。

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

    # 移入回收站（软删除）
    await trash_note(db, note)


@router.get("/{note_id}/trash-info", response_model=TrashInfoResponse)
async def get_note_trash_info(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取笔记的关联统计（删除确认弹窗文案依据）

    返回卡片数、核心卡片数（is_key_point）、双向链接数。
    回收站中的笔记也可查询（彻底删除确认弹窗依据）。
    """
    note = await get_note_detail(db, note_id, current_user.id, include_trashed=True)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")
    info = await get_trash_info(db, note)
    return TrashInfoResponse(**info)


@router.post("/{note_id}/restore", response_model=RestoreResponse)
async def restore_note_api(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    从回收站恢复笔记

    原子包整体还原：关系/卡片/题目/复习记录自动复原，文件搬回 inbox 原位。
    若原位置已有同名新文件，自动加序号后缀（base-1、base-2…），
    响应含 renamed_to 用于前端提示。

    Raises:
        HTTPException 404: 笔记不存在
        HTTPException 400: 笔记不在回收站中
        HTTPException 409: inbox 同名文件冲突，1~999 改名序号被占用，无法恢复
    """
    note = await get_note_detail(db, note_id, current_user.id, include_trashed=True)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")
    if note.trashed_at is None:
        raise HTTPException(status_code=400, detail="该笔记不在回收站中")

    try:
        restored, renamed_to = await restore_note(db, note)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return RestoreResponse(
        note=await _build_note_response(db, restored),
        renamed_to=renamed_to,
    )


@router.delete("/{note_id}/purge", status_code=status.HTTP_204_NO_CONTENT)
async def purge_note_api(
    note_id: str,
    promote_key_cards: bool = Query(False, description="是否将 is_key_point 核心卡片提升为独立节点"),
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    彻底删除笔记（物理删除，悬挂引用策略）

    关系记录（CardRelation / NoteMaterialLink）被删端置 NULL 悬挂保留，
    绝不级联删除；可选将核心卡片提升为独立节点（保留其关系/题目/复习进度）。

    Args:
        note_id: 笔记 ID
        promote_key_cards: 提升核心卡片为独立节点（图谱中保留）

    Raises:
        HTTPException 404: 笔记不存在
    """
    note = await get_note_detail(db, note_id, current_user.id, include_trashed=True)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    await purge_note(db, note, promote_key_cards=promote_key_cards)