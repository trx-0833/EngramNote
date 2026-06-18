"""
文件上传 API 模块

本模块提供文件上传和转换状态查询接口。
上传文件后自动触发 Celery 异步任务进行文档转换（Mineru/ASR）。

主要职责：
- 文件上传接口（POST /api/upload），校验文件类型和大小，保存到对象存储，触发异步转换
- 转换状态查询接口（GET /api/upload/{note_id}/status），供前端轮询转换进度

设计决策：
- 文件扩展名到 SourceType 的映射通过 _EXT_TO_SOURCE_TYPE 字典维护，便于扩展
- 上传流程：校验 → 创建笔记记录 → 保存文件 → 触发异步转换
- 使用临时文件中转上传内容，避免大文件占用内存
- Celery 不可用时标记任务失败但不删除已保存的文件，支持手动重试
- 文件存储路径格式为 {user_id}/{note_id}/{filename}，确保用户间隔离
"""

from __future__ import annotations

import os
import tempfile
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_db
from ..models.note import Note, NoteStatus, SourceType
from ..models.user import User
from ..schemas.note import NoteResponse, NoteStatusResponse
from ..api.auth import get_current_user_dependency
from ..services.storage_service import upload_file, ensure_buckets_exist
from ..tasks.convert_tasks import convert_document_task

settings = get_settings()

router = APIRouter()

# 文件扩展名到 SourceType 的映射，决定文件的处理方式
_EXT_TO_SOURCE_TYPE = {
    ".pdf": SourceType.pdf,
    ".png": SourceType.image,
    ".jpg": SourceType.image,
    ".jpeg": SourceType.image,
    ".docx": SourceType.docx,
    ".pptx": SourceType.pptx,
    ".xlsx": SourceType.xlsx,
    ".mp4": SourceType.video,
    ".mp3": SourceType.audio,
    ".wav": SourceType.audio,
    ".m4a": SourceType.audio,
}

# 允许的扩展名集合，用于上传校验
ALLOWED_EXTS = set(_EXT_TO_SOURCE_TYPE.keys())


@router.post("", response_model=NoteResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    backend: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    上传文件并触发异步转换

    完整流程：
    1. 校验文件扩展名是否在允许列表中
    2. 读取文件内容并校验大小是否超过限制
    3. 创建笔记记录（状态为 uploading）
    4. 将原始文件保存到对象存储
    5. 更新状态为 converting，触发 Celery 异步转换任务

    Args:
        file: 上传的文件对象
        backend: 解析后端选择（可选），如 "pipeline"（本地）或 "vlm-http-client"（云端），
                 为 None 时使用 config.py 中的 mineru_backend 默认值
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        NoteResponse: 新创建的笔记信息

    Raises:
        HTTPException 400: 文件名为空、文件格式不支持、文件大小超限
        HTTPException 500: 文件上传到对象存储失败
    """
    # 1. 校验文件扩展名
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {ext}，支持: {', '.join(sorted(ALLOWED_EXTS))}",
        )

    # 根据扩展名确定文件来源类型
    source_type = _EXT_TO_SOURCE_TYPE[ext]

    # 2. 流式读取文件到临时文件，同时校验大小
    max_size = settings.max_upload_size_mb * 1024 * 1024
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp_path = tmp.name
        total_size = 0
        while True:
            chunk = await file.read(1024 * 1024)  # 1MB chunks
            if not chunk:
                break
            total_size += len(chunk)
            if total_size > max_size:
                os.unlink(tmp_path)
                raise HTTPException(
                    status_code=400,
                    detail=f"文件大小超过限制 ({settings.max_upload_size_mb}MB)",
                )
            tmp.write(chunk)

    # 3. 创建笔记记录
    note_id = str(uuid.uuid4())
    # 对象存储路径格式：{user_id}/{note_id}/{filename}，确保用户间隔离
    object_name = f"{current_user.id}/{note_id}/{file.filename}"

    note = Note(
        id=note_id,
        user_id=current_user.id,
        # 默认使用文件名（不含扩展名）作为笔记标题
        title=os.path.splitext(file.filename)[0],
        source_type=source_type,
        original_file_path=object_name,
        status=NoteStatus.uploading,
        file_size=total_size,
    )
    db.add(note)
    await db.commit()
    await db.refresh(note)

    # 4. 保存原始文件到对象存储（临时文件已在步骤2创建）
    try:
        # 确保存储桶目录存在
        ensure_buckets_exist()
        upload_file(
            settings.minio_bucket_original,
            object_name,
            tmp_path,
            content_type=file.content_type or "application/octet-stream",
        )
        # upload_file_local 使用 os.replace 会移动文件，无需再删除临时文件
        # 如果是 MinIO 模式，需要手动删除临时文件
        if settings.storage_backend == "minio" and os.path.exists(tmp_path):
            os.unlink(tmp_path)
    except Exception as e:
        # 文件上传失败，标记笔记为失败状态
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        note.status = NoteStatus.failed
        note.error_message = f"文件上传失败: {str(e)}"
        await db.commit()
        raise HTTPException(status_code=500, detail=f"文件上传失败: {str(e)}")

    # 5. 更新状态为 converting 并触发 Celery 异步任务
    note.status = NoteStatus.converting
    await db.commit()
    await db.refresh(note)

    try:
        # 异步触发文档转换任务（Mineru 或 ASR），传递用户选择的解析后端
        convert_document_task.delay(note_id, object_name, source_type.value, backend=backend)
    except Exception as e:
        # Celery 不可用时标记失败，但文件已保存到对象存储，可手动重试
        note.status = NoteStatus.failed
        note.error_message = f"转换任务提交失败: {str(e)}"
        await db.commit()
        await db.refresh(note)

    return NoteResponse.model_validate(note)


@router.get("/{note_id}/status", response_model=NoteStatusResponse)
async def get_upload_status(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    查询文件转换状态

    前端可通过此接口轮询笔记的处理状态，了解转换进度。
    当状态为 failed 时，error_message 中包含失败原因。

    Args:
        note_id: 笔记 ID
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        NoteStatusResponse: 包含笔记 ID、当前状态和错误信息的响应

    Raises:
        HTTPException 404: 笔记不存在或不属于当前用户
    """
    result = await db.execute(
        select(Note).where(Note.id == note_id, Note.user_id == current_user.id)
    )
    note = result.scalars().first()
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    return NoteStatusResponse(
        id=note.id,
        status=note.status,
        error_message=note.error_message,
    )
