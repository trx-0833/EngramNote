"""
笔记清洗 API 模块

本模块提供笔记清洗相关的 HTTP 接口，包括触发清洗、查询状态、
获取 diff 数据、恢复/删除重复块等操作。

主要职责：
- 手动触发清洗任务（POST /api/cleaning/{note_id}/start）
- 停止清洗任务（POST /api/cleaning/{note_id}/stop）
- 查询清洗状态（GET /api/cleaning/{note_id}/status）
- 获取原始版与清洗版的 diff 数据（GET /api/cleaning/{note_id}/diff）
- 恢复被注释的重复块（POST /api/cleaning/{note_id}/restore/{block_index}）
- 彻底删除重复块（DELETE /api/cleaning/{note_id}/block/{block_index}）

设计决策：
- 所有接口需要用户认证，且只能操作自己的笔记
- 只有 converted、cleaned 或 cleaning_failed 状态的笔记可以触发清洗
- 恢复/删除操作直接修改存储中的清洗副本，并更新元数据
- diff 数据在服务端生成，前端直接渲染
"""

import difflib
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.note import Note, NoteStatus
from ..models.user import User
from ..api.auth import get_current_user_dependency
from ..schemas.cleaning import (
    CleaningStartResponse,
    CleaningStatusResponse,
    DiffLine,
    DiffBlock,
    CleaningDiffResponse,
    BlockOperationResponse,
    CleaningStopResponse,
)
from ..services.note_service import (
    get_note_detail,
    get_note_markdown_content,
    get_clean_markdown_content,
)
from ..services.storage_service import (
    get_object_bytes,
    upload_bytes,
)
from ..services.cleaning_service import restore_block, delete_block
from ..config import get_settings

settings = get_settings()
router = APIRouter()


# --- API 端点 ---

@router.post("/{note_id}/start", response_model=CleaningStartResponse)
async def start_cleaning(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    手动触发笔记清洗

    仅对 converted 或 cleaned 状态的笔记有效。
    对于已清洗的笔记，会重新执行清洗流程。

    Args:
        note_id: 笔记 ID
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        CleaningStartResponse: 包含笔记 ID、状态和提示信息
    """
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    if note.status not in (NoteStatus.converted, NoteStatus.cleaned, NoteStatus.cleaning_failed):
        raise HTTPException(
            status_code=400,
            detail=f"笔记当前状态为 {note.status.value}，只有 converted、cleaned 或 cleaning_failed 状态可以触发清洗",
        )

    # 先将数据库状态更新为 cleaning，避免前端刷新时仍显示旧状态
    from sqlalchemy import select
    result = await db.execute(select(Note).where(Note.id == note_id))
    db_note = result.scalars().first()
    if db_note:
        db_note.status = NoteStatus.cleaning
        db_note.error_message = None
        await db.commit()

    # 触发 Celery 清洗任务
    from ..tasks.clean_tasks import clean_document_task
    clean_document_task.delay(note_id)

    return CleaningStartResponse(
        id=note_id,
        status=NoteStatus.cleaning,
        message="清洗任务已触发",
    )


@router.post("/{note_id}/stop", response_model=CleaningStopResponse)
async def stop_cleaning(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    停止正在进行的清洗任务

    仅对 cleaning 状态的笔记有效。将笔记状态更新为 cleaning_failed，
    并记录用户手动停止的操作。

    Args:
        note_id: 笔记 ID
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        CleaningStopResponse: 包含笔记 ID、状态和提示信息
    """
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    if note.status != NoteStatus.cleaning:
        raise HTTPException(
            status_code=400,
            detail=f"笔记当前状态为 {note.status.value}，只有 cleaning 状态可以停止清洗",
        )

    # 更新笔记状态为清洗失败
    from sqlalchemy import select
    result = await db.execute(select(Note).where(Note.id == note_id))
    db_note = result.scalars().first()
    if db_note:
        db_note.status = NoteStatus.cleaning_failed
        db_note.error_message = "用户手动停止清洗"
        await db.commit()

    return CleaningStopResponse(
        id=note_id,
        status=NoteStatus.cleaning_failed,
        message="清洗已停止",
    )


@router.get("/{note_id}/status", response_model=CleaningStatusResponse)
async def get_cleaning_status(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    查询笔记清洗状态

    返回笔记的当前状态、清洗文件路径和元数据（含清洗统计信息）。

    Args:
        note_id: 笔记 ID
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        CleaningStatusResponse: 清洗状态信息
    """
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    return CleaningStatusResponse(
        id=note.id,
        status=note.status,
        clean_md_path=note.clean_md_path,
        error_message=note.error_message,
        metadata_=note.metadata_,
    )


@router.get("/{note_id}/diff", response_model=CleaningDiffResponse)
async def get_cleaning_diff(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取原始版与清洗版的 diff 数据

    使用 Python 标准库 difflib 计算行级差异，
    返回结构化的 diff 数据供前端渲染。

    Args:
        note_id: 笔记 ID
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        CleaningDiffResponse: diff 数据
    """
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    if not note.clean_md_path:
        raise HTTPException(status_code=400, detail="笔记尚未完成清洗")

    # 读取原始版和清洗版内容
    original_md = await get_note_markdown_content(note)
    clean_md = await get_clean_markdown_content(note)

    if not original_md or not clean_md:
        raise HTTPException(status_code=500, detail="无法读取 Markdown 内容")

    # 计算行级 diff
    original_lines = original_md.splitlines()
    clean_lines = clean_md.splitlines()

    # 使用 difflib.SequenceMatcher 计算差异
    matcher = difflib.SequenceMatcher(None, original_lines, clean_lines)
    diff_blocks = []
    orig_line_no = 0
    clean_line_no = 0

    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            orig_line_no = i2
            clean_line_no = j2
            continue

        block_lines = []
        # 删除的行（原始版有，清洗版无）
        for i in range(i1, i2):
            block_lines.append(DiffLine(
                type="removed",
                content=original_lines[i],
                line_number_original=i + 1,
            ))
        # 新增的行（清洗版有，原始版无）
        for j in range(j1, j2):
            block_lines.append(DiffLine(
                type="added",
                content=clean_lines[j],
                line_number_clean=j + 1,
            ))

        if block_lines:
            diff_blocks.append(DiffBlock(lines=block_lines))

        orig_line_no = i2
        clean_line_no = j2

    # 提取清洗统计信息
    clean_stats = None
    if note.metadata_ and "clean_stats" in note.metadata_:
        clean_stats = note.metadata_.get("clean_stats")
        if note.metadata_.get("copy_stats"):
            clean_stats.update(note.metadata_["copy_stats"])

    return CleaningDiffResponse(
        note_id=note_id,
        original_lines=len(original_lines),
        clean_lines=len(clean_lines),
        blocks=diff_blocks,
        stats=clean_stats,
    )


@router.post("/{note_id}/restore/{block_index}", response_model=BlockOperationResponse)
async def restore_duplicate_block(
    note_id: str,
    block_index: int,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    恢复被标记为重复的块

    移除指定块的 duplicate HTML 注释标记，使其内容正常显示。

    Args:
        note_id: 笔记 ID
        block_index: 要恢复的块序号
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        BlockOperationResponse: 操作结果
    """
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    if not note.clean_md_path:
        raise HTTPException(status_code=400, detail="笔记尚未完成清洗")

    # 读取当前清洗副本
    clean_md = await get_clean_markdown_content(note)
    if not clean_md:
        raise HTTPException(status_code=500, detail="无法读取清洗副本内容")

    # 恢复指定块
    updated_md = restore_block(clean_md, block_index)

    # 上传更新后的清洗副本
    upload_bytes(
        settings.minio_bucket_markdown,
        note.clean_md_path,
        updated_md.encode("utf-8"),
        content_type="text/markdown; charset=utf-8",
    )

    # 更新元数据中的重复块列表
    if note.metadata_ and "duplicates_detail" in note.metadata_:
        duplicates_detail = note.metadata_["duplicates_detail"]
        duplicates_detail = [
            d for d in duplicates_detail if d["block_index"] != block_index
        ]
        note.metadata_["duplicates_detail"] = duplicates_detail
        note.metadata_["duplicate_blocks"] = len(duplicates_detail)
        # 需要显式更新 metadata_ 字段
        from sqlalchemy import select
        result = await db.execute(select(Note).where(Note.id == note_id))
        db_note = result.scalars().first()
        if db_note:
            db_note.metadata_ = note.metadata_
            await db.commit()

    return BlockOperationResponse(
        note_id=note_id,
        block_index=block_index,
        operation="restored",
        message=f"块 {block_index} 已恢复",
    )


@router.delete("/{note_id}/block/{block_index}", response_model=BlockOperationResponse)
async def delete_duplicate_block(
    note_id: str,
    block_index: int,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    彻底删除被标记为重复的块

    连同内容和注释标记一起删除，不可恢复。

    Args:
        note_id: 笔记 ID
        block_index: 要删除的块序号
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        BlockOperationResponse: 操作结果
    """
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    if not note.clean_md_path:
        raise HTTPException(status_code=400, detail="笔记尚未完成清洗")

    # 读取当前清洗副本
    clean_md = await get_clean_markdown_content(note)
    if not clean_md:
        raise HTTPException(status_code=500, detail="无法读取清洗副本内容")

    # 删除指定块
    updated_md = delete_block(clean_md, block_index)

    # 上传更新后的清洗副本
    upload_bytes(
        settings.minio_bucket_markdown,
        note.clean_md_path,
        updated_md.encode("utf-8"),
        content_type="text/markdown; charset=utf-8",
    )

    # 更新元数据中的重复块列表
    if note.metadata_ and "duplicates_detail" in note.metadata_:
        duplicates_detail = note.metadata_["duplicates_detail"]
        duplicates_detail = [
            d for d in duplicates_detail if d["block_index"] != block_index
        ]
        note.metadata_["duplicates_detail"] = duplicates_detail
        note.metadata_["duplicate_blocks"] = len(duplicates_detail)
        from sqlalchemy import select
        result = await db.execute(select(Note).where(Note.id == note_id))
        db_note = result.scalars().first()
        if db_note:
            db_note.metadata_ = note.metadata_
            await db.commit()

    return BlockOperationResponse(
        note_id=note_id,
        block_index=block_index,
        operation="deleted",
        message=f"块 {block_index} 已删除",
    )
