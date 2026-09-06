"""
笔记列表、详情、内容与归档 API（子路由模块）

原 api/notes.py 拆分出的第一块：笔记列表、详情、内容编辑与归档。
所有接口均通过 get_current_user_dependency 确保用户已认证。
笔记是系统的核心资源，用户只能访问自己创建的笔记。
"""

import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_db
from ...models.note import Note, NoteRole, NoteStatus, SourceType
from ...models.user import User
from ...schemas.note import (
    NoteContentUpdateRequest,
    NoteDetailResponse,
    NoteListResponse,
    NoteResponse,
    NoteUpdateRequest,
)
from ...config import get_settings
from ...services import note_service
from ...services.note_service import (
    get_clean_markdown_content,
    get_note_detail,
    get_note_markdown_content,
    get_notes_list,
    update_note,
)
from ...services.storage_service import _resolve_path, get_presigned_url
from ...api.auth import get_current_user_dependency

from ._common import (
    _build_note_response,
    _build_note_responses,
    _fill_project_names,
    _load_project_tags,
)

settings = get_settings()

router = APIRouter()


@router.get("", response_model=NoteListResponse)
async def list_notes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    keyword: Optional[str] = None,
    note_role: Optional[str] = Query(None, description="笔记角色过滤：material 或 personal_note"),
    project_id: Optional[str] = Query(None, description="按项目过滤"),
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取笔记列表

    返回当前用户的笔记列表，支持分页和标题关键词搜索。
    按创建时间倒序排列（最新笔记在前）。

    Args:
        page: 页码，从 1 开始，默认第 1 页
        page_size: 每页数量，默认 20，最大 1000（项目"添加笔记"面板一次拉取候选需要）
        keyword: 搜索关键词，按标题模糊匹配（可选）
        note_role: 笔记角色过滤（可选），material 或 personal_note
        project_id: 按项目过滤（可选）
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        NoteListResponse: 包含笔记列表、总数、分页信息的响应
    """
    if note_role is not None:
        valid_roles = [e.value for e in NoteRole]
        if note_role not in valid_roles:
            raise HTTPException(
                status_code=400,
                detail=f"无效的 note_role 值: {note_role}，有效值为: {', '.join(valid_roles)}",
            )
    notes, total = await get_notes_list(
        db, current_user.id, page, page_size, keyword, note_role=note_role, project_id=project_id
    )
    items = await _build_note_responses(db, notes)
    return NoteListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/archive", response_model=NoteListResponse)
async def list_archived_notes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    note_role: Optional[str] = Query(None, description="笔记角色过滤：material 或 personal_note"),
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取已归档笔记列表

    按创建时间倒序排列，专门展示已归档的笔记。
    """
    if note_role is not None:
        valid_roles = [e.value for e in NoteRole]
        if note_role not in valid_roles:
            raise HTTPException(
                status_code=400,
                detail=f"无效的 note_role 值: {note_role}，有效值为: {', '.join(valid_roles)}",
            )
    notes, total = await get_notes_list(
        db, current_user.id, page, page_size, note_status=NoteStatus.archived, note_role=note_role
    )
    items = await _build_note_responses(db, notes)
    return NoteListResponse(
        items=items,
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
    tags = await _load_project_tags(db, [note])
    _fill_project_names(resp, note, tags)
    if note.source_type == SourceType.video:
        resp.video_url = f"/api/notes/{note.id}/video"
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
    return await _build_note_response(db, updated)


@router.put("/{note_id}/content", response_model=NoteResponse)
async def update_note_content(
    note_id: str,
    req: NoteContentUpdateRequest,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    更新笔记的 Markdown 内容

    将用户编辑的 Markdown 内容保存到对象存储，覆盖原有文件。
    处理中状态（uploading/converting/cleaning/learning）的笔记不可编辑。
    """
    # 1. 校验笔记归属
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    # 2. 校验处理中状态
    processing_statuses = {
        NoteStatus.uploading, NoteStatus.converting,
        NoteStatus.cleaning, NoteStatus.learning,
    }
    if note.status in processing_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"笔记正在处理中（{note.status.value}），暂不可编辑",
        )

    # 2.1 原始版内容不可编辑（只读），仅允许编辑清洗版
    if req.target == "original":
        raise HTTPException(
            status_code=400,
            detail="原始版内容不可编辑，请切换到清洗版后编辑",
        )

    # 3. 校验内容大小（5MB 限制，按字节数计算）
    content_size = len(req.content.encode("utf-8"))
    if content_size > 5 * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail="内容过大（超过 5MB），请缩短后重试",
        )

    # 4. 保存内容
    success = await note_service.save_note_content(db, note, req.content, req.target)
    if not success:
        raise HTTPException(
            status_code=400,
            detail="目标 Markdown 路径为空，资料可能尚未转换完成",
        )

    # 5. 返回更新后的笔记信息
    return await _build_note_response(db, note)


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
        # F-26 修复：取消归档恢复原状态语义——
        # converted 笔记归档后取消应回到 converted（从未清洗，不能谎称 cleaned）；
        # 其余（cleaned/learning_failed）回到 cleaned。
        # 判断依据：clean_md_path 是否存在（该笔记是否产出过清洗副本）。
        if note.clean_md_path:
            note.status = NoteStatus.cleaned
        else:
            note.status = NoteStatus.converted
    else:
        note.status = NoteStatus.archived
    note.error_message = None
    await db.commit()
    await db.refresh(note)
    # 归档状态变更同步写穿 meta 镜像
    from ...services.vault_meta import write_note_meta
    write_note_meta(note)
    return await _build_note_response(db, note)


@router.patch("/{note_id}/role", response_model=NoteResponse)
async def update_note_role(
    note_id: str,
    note_role: str = Query(..., description="笔记角色：material 或 personal_note"),
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    更新笔记角色

    允许在 material（学习资料）和 personal_note（我的笔记）之间切换。

    Args:
        note_id: 笔记 ID
        note_role: 新的笔记角色值
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        NoteResponse: 更新后的笔记信息

    Raises:
        HTTPException 404: 笔记不存在或不属于当前用户
        HTTPException 400: note_role 值无效
    """
    # 验证 note_role 值是否合法
    valid_roles = [e.value for e in NoteRole]
    if note_role not in valid_roles:
        raise HTTPException(
            status_code=400,
            detail=f"无效的 note_role 值: {note_role}，有效值为: {', '.join(valid_roles)}",
        )

    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    note.note_role = NoteRole(note_role)
    await db.commit()
    await db.refresh(note)
    return await _build_note_response(db, note)


def _resolve_storage_path(bucket: str, object_name: str) -> Path:
    """将 bucket/object_name 映射为本地文件系统的绝对路径"""
    return _resolve_path(bucket, object_name)


@router.get("/{note_id}/video")
async def stream_video(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    """
    流式传输视频文件

    支持视频进度条拖拽（HTTP Range 请求）。
    MinIO 模式下重定向到预签名 URL，本地模式下流式返回文件内容。

    Args:
        note_id: 笔记 ID
        current_user: 当前认证用户
        db: 异步数据库会话
        request: 原始 HTTP 请求（用于读取 Range 头）

    Returns:
        StreamingResponse 或 RedirectResponse

    Raises:
        HTTPException 404: 笔记不存在
        HTTPException 400: 笔记不是视频类型
        HTTPException 404: 视频文件不存在
    """
    # 1. 验证笔记归属和类型
    result = await db.execute(
        select(Note).where(Note.id == note_id, Note.user_id == current_user.id)
    )
    note = result.scalars().first()
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")
    if note.source_type != SourceType.video:
        raise HTTPException(status_code=400, detail="该笔记不是视频类型")

    # 2. MinIO 模式：生成预签名 URL 并重定向
    if settings.storage_backend == "minio":
        url = get_presigned_url(settings.minio_bucket_original, note.original_file_path, expires_hours=1)
        return RedirectResponse(url=url)

    # 3. 本地存储：流式返回文件，支持 Range 请求
    video_path = _resolve_storage_path(settings.minio_bucket_original, note.original_file_path)
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="视频文件不存在")

    file_size = video_path.stat().st_size

    # 处理 Range 请求（支持视频进度条拖拽）
    range_header = request.headers.get("range") if request else None

    if range_header:
        # 解析 Range 头（例如 "bytes=0-1023"）
        range_match = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if range_match:
            start = int(range_match.group(1))
            end = int(range_match.group(2)) if range_match.group(2) else file_size - 1
            end = min(end, file_size - 1)
            content_length = end - start + 1

            def iter_file():
                with open(str(video_path), "rb") as f:
                    f.seek(start)
                    remaining = content_length
                    while remaining > 0:
                        chunk_size = min(8192, remaining)
                        data = f.read(chunk_size)
                        if not data:
                            break
                        remaining -= len(data)
                        yield data

            return StreamingResponse(
                iter_file(),
                status_code=206,
                media_type="video/mp4",
                headers={
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(content_length),
                },
            )

    # 无 Range 头：流式返回整个文件
    def iter_file():
        with open(str(video_path), "rb") as f:
            while True:
                data = f.read(8192)
                if not data:
                    break
                yield data

    return StreamingResponse(
        iter_file(),
        media_type="video/mp4",
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
        },
    )