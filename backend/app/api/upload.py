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
- 文件存储路径格式为 {user_id}/{project_slug}/source/...（未选择项目时为 {user_id}/inbox/source/...），确保用户间隔离
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import logging

from ..config import get_settings
from ..database import get_db
from ..models.note import Note, NoteRole, NoteStatus, SourceType
from ..models.folder import Folder
from ..models.project import Project
from ..models.user import User
from ..schemas.note import NoteResponse, NoteStatusResponse
from ..api.auth import get_current_user_dependency
from ..services import note_service
from ..services import vault_path
from ..services.storage_service import upload_file, ensure_buckets_exist
from ..services.vault_meta import write_note_meta
from ..tasks.convert_tasks import convert_document_task

settings = get_settings()
logger = logging.getLogger(__name__)

router = APIRouter()

# 文件扩展名到 SourceType 的映射，决定文件的处理方式
# 单一来源为 vault_path.EXT_TO_SOURCE_TYPE（upload 与扫描导入共用）
_EXT_TO_SOURCE_TYPE = {
    ext: SourceType(v) for ext, v in vault_path.EXT_TO_SOURCE_TYPE.items()
}

# 允许的扩展名集合，用于上传校验
ALLOWED_EXTS = vault_path.ALLOWED_EXTS

# 文件扩展名 → 内容签名（魔术字节）校验规则，防止伪装文件（如 .png 内嵌 HTML）上传
# prefix: 文件头必须以此字节序列开头；contains: 文件头 32 字节内必须包含该字节序列
# 未配置规则的类型（如 .md 纯文本、mkv、mp3 等）跳过内容校验
_MAGIC_RULES = {
    ".pdf": (b"%PDF-", None),
    ".png": (b"\x89PNG\r\n\x1a\n", None),
    ".jpg": (b"\xff\xd8\xff", None),
    ".jpeg": (b"\xff\xd8\xff", None),
    # Office 文档为 ZIP 容器
    ".docx": (b"PK\x03\x04", None),
    ".pptx": (b"PK\x03\x04", None),
    ".xlsx": (b"PK\x03\x04", None),
    # MP4/MOV 容器以 ftyp box 开头
    ".mp4": (None, b"ftyp"),
    ".mov": (None, b"ftyp"),
}
# 需要读取用于签名校验的头部字节数
_MAGIC_HEAD_SIZE = 32


def _validate_magic(ext: str, head: bytes) -> bool:
    """
    校验文件内容签名（魔术字节）与扩展名是否匹配

    Args:
        ext: 文件扩展名（小写，含点）
        head: 文件头部字节

    Returns:
        bool: 匹配返回 True；未配置规则的扩展名视为通过
    """
    rule = _MAGIC_RULES.get(ext)
    if rule is None:
        return True
    prefix, contains = rule
    if prefix is not None:
        return head.startswith(prefix)
    if contains is not None:
        return contains in head[: _MAGIC_HEAD_SIZE]
    return True


async def _resolve_unique_base(
    db: AsyncSession,
    user_id: str,
    prefix: str,
    safe_stem: str,
    ext: str,
) -> str:
    """
    生成不冲突的文件主干（base），保持磁盘文件名可读

    优先保留原始文件名（与扫描导入命名一致）；同 prefix/source 下已存在同名文件时，
    追加随机后缀防冲突。存量笔记路径已存数据库，不受影响。

    Args:
        db: 数据库会话
        user_id: 用户 ID
        prefix: 项目前缀 {user_id}/{project_slug}
        safe_stem: 清洗后的原始文件名主干
        ext: 文件扩展名（小写，含点）

    Returns:
        str: 不冲突的 base
    """
    # 空或特殊主干（"."、".."）直接使用随机名，避免生成易混淆或边界文件名
    if not safe_stem or safe_stem in (".", ".."):
        safe_stem = os.urandom(4).hex()

    async def _exists(candidate: str) -> bool:
        object_name = vault_path.source_object(prefix, candidate, ext)
        result = await db.execute(
            select(Note.id).where(
                Note.user_id == user_id,
                Note.original_file_path == object_name,
            )
        )
        return result.scalars().first() is not None

    if not await _exists(safe_stem):
        return safe_stem
    # 同名已存在：追加 2 字节（4 位十六进制）随机后缀，最多尝试 5 次
    for _ in range(5):
        candidate = f"{safe_stem}_{os.urandom(2).hex()}"
        if not await _exists(candidate):
            return candidate
    # 兜底：追加 4 字节（8 位十六进制）随机后缀
    return f"{safe_stem}_{os.urandom(4).hex()}"


@router.post("", response_model=NoteResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    backend: Optional[str] = Form(None),
    folder_id: Optional[str] = Form(None),
    project_id: Optional[str] = Form(None),
    note_role: Optional[str] = Form("material"),
    linked_material_ids: Optional[str] = Form(None),
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
        folder_id: 所属文件夹 ID（可选），上传文件归入指定文件夹
        project_id: 所属项目 ID（可选），上传文件归入指定项目；
                    留空时不归属任何项目（project_id 为 None），物理前缀为 inbox
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

    # 1.5 解析所属项目：未指定时不归属任何项目（物理前缀为 inbox）
    project = None
    if project_id:
        proj_result = await db.execute(
            select(Project).where(Project.id == project_id, Project.user_id == current_user.id)
        )
        project = proj_result.scalars().first()
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在")

    # 2. 流式读取文件到临时文件，同时校验大小、累积头部字节并计算 SHA-256 哈希
    max_size = settings.max_upload_size_mb * 1024 * 1024
    sha256 = hashlib.sha256()
    head = b""
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
            if len(head) < _MAGIC_HEAD_SIZE:
                head += chunk[:_MAGIC_HEAD_SIZE - len(head)]
            sha256.update(chunk)
            tmp.write(chunk)
    file_hash = sha256.hexdigest()

    # 2.5 文件内容签名校验（防止伪装文件上传）
    if not _validate_magic(ext, head):
        os.unlink(tmp_path)
        raise HTTPException(
            status_code=400,
            detail=f"文件内容与格式不匹配，不是有效的 {ext} 文件",
        )

    # 2.6 每用户存储配额校验（防止磁盘被写满）
    quota = settings.max_storage_per_user_mb * 1024 * 1024
    if quota > 0:
        used_result = await db.execute(
            select(func.coalesce(func.sum(Note.file_size), 0)).where(Note.user_id == current_user.id)
        )
        used = used_result.scalar_one()
        if used + total_size > quota:
            os.unlink(tmp_path)
            raise HTTPException(
                status_code=400,
                detail=f"存储空间不足：已使用 {used // (1024 * 1024)}MB，配额 {settings.max_storage_per_user_mb}MB",
            )

    # 3. 创建笔记记录
    note_id = str(uuid.uuid4())

    # 从上传文件名提取主干（命名关联法则：md 名与 source 名同主干，仅扩展名不同）
    # 1. 取文件名（不含扩展名），清理非法字符并截断到 50 字符
    # 2. 优先保留原始文件名（可读）；同名已存在时由 _resolve_unique_base 追加随机后缀
    raw_name = os.path.splitext(file.filename)[0]
    safe_stem = "".join(c for c in raw_name if c.isalnum() or c in ('-', '_', ' ', '.')).strip()[:50]
    safe_ext = os.path.splitext(file.filename)[1].lower()

    # Vault 对象路径：{user_id}/{project_slug}/source/{base}{ext}；无项目时落到 {user_id}/inbox
    prefix = vault_path.project_prefix(current_user.id, project.slug) if project else vault_path.inbox_prefix(current_user.id)
    base = await _resolve_unique_base(db, current_user.id, prefix, safe_stem, safe_ext)
    object_name = vault_path.source_object(prefix, base, safe_ext)

    # 校验 note_role 值是否合法
    try:
        note_role_enum = NoteRole(note_role)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效的 note_role 值: {note_role}")

    note = Note(
        id=note_id,
        user_id=current_user.id,
        # 默认使用文件名（不含扩展名）作为笔记标题
        title=os.path.splitext(file.filename)[0],
        source_type=source_type,
        original_file_path=object_name,
        status=NoteStatus.uploading,
        file_size=total_size,
        folder_id=folder_id,
        project_id=project.id if project else None,
        note_role=note_role_enum,
        metadata_={"file_hash": file_hash},
    )
    db.add(note)
    await db.commit()
    await db.refresh(note)
    # 若设置了文件夹，补充 folder_name 供 meta 旁载镜像记录（磁盘/离线浏览可追溯逻辑归属）
    if note.folder_id:
        folder_result = await db.execute(
            select(Folder.name).where(Folder.id == note.folder_id, Folder.user_id == current_user.id)
        )
        folder_name = folder_result.scalar_one_or_none()
        if folder_name:
            note._folder_name = folder_name
    write_note_meta(note)

    # 如果是个人笔记且传入了关联资料 ID，创建笔记-资料链接
    if note_role == "personal_note" and linked_material_ids:
        try:
            material_ids = json.loads(linked_material_ids)
            if isinstance(material_ids, list) and material_ids:
                # 限制数量
                if len(material_ids) > 50:
                    material_ids = material_ids[:50]
                await note_service.create_note_material_links(
                    db, current_user.id, note.id, material_ids
                )
        except json.JSONDecodeError:
            pass  # 忽略无效 JSON

    logger.info(
        "文件上传成功: user_id=%s, filename=%s, size=%d, source_type=%s",
        current_user.id, file.filename, total_size, source_type.value,
    )

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
        await db.refresh(note)
        write_note_meta(note)
        logger.error("文件上传失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="文件上传失败，请稍后重试")

    # 5. 更新状态为 converting 并触发 Celery 异步任务
    note.status = NoteStatus.converting
    await db.commit()
    await db.refresh(note)
    write_note_meta(note)

    try:
        # 异步触发文档转换任务（Mineru 或 ASR），传递用户选择的解析后端
        convert_document_task.delay(note_id, object_name, source_type.value, backend=backend)
    except Exception as e:
        # Celery 不可用时标记失败，但文件已保存到对象存储，可手动重试
        note.status = NoteStatus.failed
        note.error_message = f"转换任务提交失败: {str(e)}"
        await db.commit()
        await db.refresh(note)
        write_note_meta(note)

    resp = NoteResponse.model_validate(note)
    if note.source_type == SourceType.video:
        resp.video_url = f"/api/notes/{note.id}/video"
    return resp


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


@router.post("/{note_id}/retry", response_model=NoteStatusResponse)
async def retry_convert(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    重试转换失败的笔记

    当笔记转换失败时（status 为 failed），前端可调用此接口重新触发转换。
    会清除之前的错误信息，将状态重置为 converting，并重新提交 Celery 任务。

    Args:
        note_id: 笔记 ID
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        NoteStatusResponse: 重试后的笔记状态

    Raises:
        HTTPException 404: 笔记不存在或不属于当前用户
        HTTPException 400: 笔记状态不允许重试（非 failed 状态）
    """
    result = await db.execute(
        select(Note).where(Note.id == note_id, Note.user_id == current_user.id)
    )
    note = result.scalars().first()
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    # 仅允许 failed 状态重试
    if note.status != NoteStatus.failed:
        raise HTTPException(
            status_code=400,
            detail=f"当前状态为 {note.status.value}，仅失败状态可重试",
        )

    # 清除错误信息，重置状态
    note.error_message = None
    note.status = NoteStatus.converting
    await db.commit()
    await db.refresh(note)
    write_note_meta(note)

    # 重新提交转换任务
    try:
        convert_document_task.delay(
            note.id,
            note.original_file_path,
            note.source_type.value,
        )
    except Exception as e:
        note.status = NoteStatus.failed
        note.error_message = f"重试提交失败: {str(e)}"
        await db.commit()
        await db.refresh(note)
        write_note_meta(note)

    logger.info("重试转换: note_id=%s, source_type=%s", note_id, note.source_type.value)

    return NoteStatusResponse(
        id=note.id,
        status=note.status,
        error_message=note.error_message,
    )
