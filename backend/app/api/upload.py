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
import logging
import mimetypes
import os
import re
import shutil
import tempfile
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import TMP_UPLOAD_DIR, get_settings
from ..database import get_db
from ..models.note import Note, NoteRole, NoteStatus, SourceType
from ..models.folder import Folder
from ..models.note_project import NoteProject
from ..models.project import Project
from ..models.user import User
from ..schemas.note import NoteResponse, NoteStatusResponse
from ..api.auth import get_current_user_dependency
from ..services import note_service
from ..services import pdf_crop
from ..services import vault_path
from ..services.storage_service import upload_file, ensure_buckets_exist
from ..services.vault_meta import write_note_meta
from ..tasks.convert_tasks import convert_document_task

settings = get_settings()
logger = logging.getLogger(__name__)

router = APIRouter()

# 临时上传标识（temp_id）格式：标准 UUID 36 字符
_TEMP_ID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

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


async def _stream_upload(
    file: UploadFile, dest_path: str, max_size: int
) -> tuple[int, str, bytes]:
    """
    流式读取上传文件到指定路径，同时校验大小、累积头部字节并计算 SHA-256

    Args:
        file: 上传文件对象
        dest_path: 落盘目标路径
        max_size: 大小上限（字节），超限抛 HTTPException 并删除已写入文件

    Returns:
        tuple: (total_size, sha256_hex, head_bytes)

    Raises:
        HTTPException 400: 文件大小超过限制
    """
    sha256 = hashlib.sha256()
    head = b""
    total_size = 0
    try:
        with open(dest_path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > max_size:
                    raise HTTPException(
                        status_code=400,
                        detail=f"文件大小超过限制 ({max_size // (1024 * 1024)}MB)",
                    )
                if len(head) < _MAGIC_HEAD_SIZE:
                    head += chunk[:_MAGIC_HEAD_SIZE - len(head)]
                sha256.update(chunk)
                out.write(chunk)
    except Exception:
        if os.path.exists(dest_path):
            os.unlink(dest_path)
        raise
    return total_size, sha256.hexdigest(), head


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
        # F-16 修复：按 base（主干）检测冲突，而非仅同扩展名。
        # 旧实现按 `source_object(prefix, candidate, ext)`（含扩展名）查重，
        # 导致 a.pdf 与 a.md 判定互不冲突，但两者转换输出均为
        # output/markdown/a.md，后上传者覆盖先上传者的转换结果。
        # 现改为：同 prefix 下存在任意扩展名的同名 base 即视为冲突。
        # 注意：candidate 可能含 `_`/`%`，需转义 LIKE 通配符。
        escaped = candidate.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        prefix_match = f"{prefix}/source/{escaped}."
        result = await db.execute(
            select(Note.id).where(
                Note.user_id == user_id,
                Note.original_file_path.like(f"{prefix_match}%", escape="\\"),
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


async def _do_upload(
    db: AsyncSession,
    current_user: User,
    tmp_path: str,
    filename: str,
    source_type: SourceType,
    file_hash: str,
    content_type: str,
    backend: Optional[str],
    folder_id: Optional[str],
    project_ids: Optional[str],
    note_role: Optional[str],
    linked_material_ids: Optional[str],
) -> NoteResponse:
    """
    上传核心流程：把已校验并落盘的临时文件入库并触发异步转换

    供两种入口复用：
    - POST /api/upload（单次直传，校验在本函数之前完成）
    - POST /api/upload/commit（两阶段上传，含可选 PDF 裁剪）

    完整流程：配额校验 → 解析所属项目标签 → 创建笔记记录 → 保存原始文件到对象存储
    → 更新状态为 converting 并触发 Celery 异步转换任务。

    Args:
        db: 异步数据库会话
        current_user: 当前认证用户
        tmp_path: 已校验的临时文件路径（本地存储模式下会被移动，勿重复使用）
        filename: 原始文件名（决定笔记标题与存储 base）
        source_type: 文件来源类型
        file_hash: 文件 SHA-256
        content_type: 存储时使用的 MIME 类型
        backend: 解析后端选择（可选）
        folder_id: 所属文件夹 ID（可选）
        project_ids: 项目标签 ID 数组的 JSON 字符串（可选，支持多标签）
        note_role: 笔记角色（默认 material）
        linked_material_ids: 关联资料 ID JSON 字符串（可选，仅个人笔记）

    Returns:
        NoteResponse: 新创建的笔记信息

    Raises:
        HTTPException 400/404/500: 见各子步骤说明
    """
    ext = os.path.splitext(filename)[1].lower()
    total_size = os.path.getsize(tmp_path)

    # 1. 每用户存储配额校验（防止磁盘被写满）
    quota = settings.max_storage_per_user_mb * 1024 * 1024
    if quota > 0:
        used_result = await db.execute(
            select(func.coalesce(func.sum(Note.file_size), 0)).where(Note.user_id == current_user.id)
        )
        used = used_result.scalar_one()
        if used + total_size > quota:
            raise HTTPException(
                status_code=400,
                detail=f"存储空间不足：已使用 {used // (1024 * 1024)}MB，配额 {settings.max_storage_per_user_mb}MB",
            )

    # 2. 解析项目标签数组（JSON 字符串，支持多标签）；未指定时不归属任何项目
    selected_project_ids: list = []
    if project_ids:
        try:
            parsed = json.loads(project_ids)
            if isinstance(parsed, list):
                selected_project_ids = [str(p) for p in parsed if p][:10]
        except json.JSONDecodeError:
            selected_project_ids = []
    projects: list = []
    if selected_project_ids:
        proj_result = await db.execute(
            select(Project).where(
                Project.id.in_(selected_project_ids),
                Project.user_id == current_user.id,
            )
        )
        projects = list(proj_result.scalars().all())

    # 2.5 校验文件夹归属（F-08 修复）：folder_id 必须存在且属于当前用户，
    # 防止跨用户把笔记挂入他人文件夹（IDOR）
    if folder_id:
        folder_result = await db.execute(
            select(Folder).where(
                Folder.id == folder_id,
                Folder.user_id == current_user.id,
            )
        )
        if not folder_result.scalars().first():
            raise HTTPException(
                status_code=400,
                detail="文件夹不存在或无权访问",
            )

    # 3. 创建笔记记录
    note_id = str(uuid.uuid4())

    # 从上传文件名提取主干（命名关联法则：md 名与 source 名同主干，仅扩展名不同）
    # 1. 取文件名（不含扩展名），清理非法字符并截断到 50 字符
    # 2. 优先保留原始文件名（可读）；同名已存在时由 _resolve_unique_base 追加随机后缀
    raw_name = os.path.splitext(filename)[0]
    safe_stem = "".join(c for c in raw_name if c.isalnum() or c in ('-', '_', ' ', '.')).strip()[:50]

    # Vault 对象路径：标签化后所有笔记统一落在 {user_id}/inbox/source/{base}{ext}
    prefix = vault_path.inbox_prefix(current_user.id)
    base = await _resolve_unique_base(db, current_user.id, prefix, safe_stem, ext)
    object_name = vault_path.source_object(prefix, base, ext)

    # 校验 note_role 值是否合法
    try:
        note_role_enum = NoteRole(note_role)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效的 note_role 值: {note_role}")

    note = Note(
        id=note_id,
        user_id=current_user.id,
        # 默认使用文件名（不含扩展名）作为笔记标题
        title=os.path.splitext(filename)[0],
        source_type=source_type,
        original_file_path=object_name,
        status=NoteStatus.uploading,
        file_size=total_size,
        folder_id=folder_id,
        note_role=note_role_enum,
        metadata_={"file_hash": file_hash},
    )
    db.add(note)
    # 打上项目标签（多对多关联表，唯一约束防重复）
    for project in projects:
        db.add(NoteProject(
            note_id=note.id,
            project_id=project.id,
            user_id=current_user.id,
        ))
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
    # 注入项目标签供 meta 旁载镜像记录
    note._project_ids = [p.id for p in projects]
    note._project_names = [p.name for p in projects]
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
        current_user.id, filename, total_size, source_type.value,
    )

    # 4. 保存原始文件到对象存储（临时文件已落盘）
    try:
        # 确保存储桶目录存在
        ensure_buckets_exist()
        upload_file(
            settings.minio_bucket_original,
            object_name,
            tmp_path,
            content_type=content_type or "application/octet-stream",
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


@router.post("", response_model=NoteResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    backend: Optional[str] = Form(None),
    folder_id: Optional[str] = Form(None),
    project_ids: Optional[str] = Form(None),
    note_role: Optional[str] = Form("material"),
    linked_material_ids: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    上传文件并触发异步转换（单次直传，兼容路径）

    完整流程：
    1. 校验文件扩展名是否在允许列表中
    2. 流式读取文件内容，校验大小与内容签名（魔术字节），计算 SHA-256
    3. 复用 _do_upload 完成入库、保存到对象存储并触发异步转换

    Args:
        file: 上传的文件对象
        backend: 解析后端选择（可选），如 "pipeline"（本地）或 "vlm-http-client"（云端），
                 为 None 时使用 config.py 中的 mineru_backend 默认值
        folder_id: 所属文件夹 ID（可选），上传文件归入指定文件夹
        project_ids: 项目标签 ID 数组的 JSON 字符串（可选），上传文件打上多个项目标签；
                    留空时不归属任何项目，物理前缀统一为 inbox
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        NoteResponse: 新创建的笔记信息

    Raises:
        HTTPException 400: 文件名为空、文件格式不支持、文件大小超限、内容签名不匹配
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

    # 2. 流式读取文件到临时文件，同时校验大小、累积头部字节并计算 SHA-256 哈希
    max_size = settings.max_upload_size_mb * 1024 * 1024
    fd, tmp_path = tempfile.mkstemp(suffix=ext)
    os.close(fd)
    try:
        _, file_hash, head = await _stream_upload(file, tmp_path, max_size)

        # 2.5 文件内容签名校验（防止伪装文件上传）
        if not _validate_magic(ext, head):
            raise HTTPException(
                status_code=400,
                detail=f"文件内容与格式不匹配，不是有效的 {ext} 文件",
            )

        # 3. 复用核心流程：配额校验 → 入库 → 保存 → 触发转换
        return await _do_upload(
            db, current_user, tmp_path, file.filename, source_type,
            file_hash, file.content_type or "application/octet-stream",
            backend, folder_id, project_ids, note_role, linked_material_ids,
        )
    finally:
        # 本地存储模式下 _do_upload 已把文件移动到 vault；MinIO 模式也已删除
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


@router.post("/prepare")
async def prepare_upload(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user_dependency),
):
    """
    两阶段上传阶段 1：接收文件并暂存，返回页数等信息

    文件落盘到临时目录 data/tmp/upload/{uuid}/{原文件名}，等待 commit 消费。
    本阶段只做基础校验（扩展名/大小/内容签名），配额校验在 commit 阶段完成。

    Args:
        file: 上传的文件对象
        current_user: 当前认证用户

    Returns:
        dict: {"temp_id", "filename", "source_type", "page_count"}
              PDF 返回 page_count，其他格式返回 null

    Raises:
        HTTPException 400: 文件名不合法、格式不支持、大小超限、内容签名不匹配、PDF 解析失败
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    # 拒绝带路径分隔符的文件名，防止路径穿越
    filename = os.path.basename(file.filename)
    if filename != file.filename:
        raise HTTPException(status_code=400, detail="文件名不合法")

    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {ext}，支持: {', '.join(sorted(ALLOWED_EXTS))}",
        )

    # 根据扩展名确定文件来源类型
    source_type = _EXT_TO_SOURCE_TYPE[ext]

    # 流式读取到临时目录，同时校验大小、累积头部字节并计算 SHA-256
    temp_id = str(uuid.uuid4())
    temp_dir = TMP_UPLOAD_DIR / temp_id
    temp_dir.mkdir(parents=True, exist_ok=True)
    dest = temp_dir / filename
    max_size = settings.max_upload_size_mb * 1024 * 1024
    try:
        _, file_hash, head = await _stream_upload(file, str(dest), max_size)
        if not _validate_magic(ext, head):
            raise HTTPException(
                status_code=400,
                detail=f"文件内容与格式不匹配，不是有效的 {ext} 文件",
            )
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    # PDF 返回页数供前端裁剪配置使用；其他格式返回 null（本轮不支持分页）
    page_count = None
    if source_type == SourceType.pdf:
        try:
            page_count = pdf_crop.get_pdf_page_count(str(dest))
        except ValueError as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=f"PDF 解析失败: {e}")

    logger.info(
        "上传暂存成功: user_id=%s, filename=%s, size=%d, source_type=%s, page_count=%s",
        current_user.id, filename, os.path.getsize(dest), source_type.value, page_count,
    )

    return {
        "temp_id": temp_id,
        "filename": filename,
        "source_type": source_type.value,
        "page_count": page_count,
    }


@router.post("/commit", response_model=NoteResponse, status_code=status.HTTP_201_CREATED)
async def commit_upload(
    temp_id: str = Form(...),
    filename: Optional[str] = Form(None),
    crop_page_range: Optional[str] = Form(None),
    backend: Optional[str] = Form(None),
    folder_id: Optional[str] = Form(None),
    project_ids: Optional[str] = Form(None),
    note_role: Optional[str] = Form("material"),
    linked_material_ids: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    两阶段上传阶段 2：消费临时文件，可选按页裁剪 PDF，然后入库并触发转换

    流程：
    1. 按 temp_id 定位临时目录（严格校验 UUID 格式，防止路径穿越）
    2. 若为 PDF 且提供了 crop_page_range：解析页范围并裁剪出新的 PDF
    3. 复用 _do_upload 完成入库、保存到对象存储并触发异步转换
    4. 无论成功失败，finally 清理临时目录

    说明：filename 用于重命名上传后的笔记/存储文件。只允许修改文件名主干，
    扩展名必须与真实文件类型一致（避免类型伪装）；不传则沿用原文件名。

    Args:
        temp_id: prepare 阶段返回的临时上传标识
        filename: 重命名后的文件名（可选），扩展名需与原文件一致
        crop_page_range: 页码范围表达式（如 "1-20,25,30-32"），仅 PDF 支持
        backend: 解析后端选择（可选）
        folder_id: 所属文件夹 ID（可选）
        project_ids: 项目标签 ID 数组的 JSON 字符串（可选），支持多标签
        note_role: 笔记角色（默认 material）
        linked_material_ids: 关联资料 ID JSON 字符串（可选）
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        NoteResponse: 新创建的笔记信息

    Raises:
        HTTPException 400: temp_id 无效或已过期、文件名不合法/扩展名不一致、
                           页范围非法、非 PDF 却指定裁剪
        HTTPException 404: 项目不存在
    """
    # 1. 严格校验 temp_id 为 UUID，防止路径穿越
    if not _TEMP_ID_RE.fullmatch(temp_id):
        raise HTTPException(status_code=400, detail="无效的临时上传标识")
    temp_dir = TMP_UPLOAD_DIR / temp_id
    if not temp_dir.is_dir():
        raise HTTPException(status_code=400, detail="临时上传已失效，请重新选择文件")

    files = [p for p in temp_dir.iterdir() if p.is_file()]
    if len(files) != 1:
        raise HTTPException(status_code=400, detail="临时上传数据异常，请重新选择文件")
    src_path = files[0]
    ext = src_path.suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(status_code=400, detail="临时文件格式不受支持")
    source_type = _EXT_TO_SOURCE_TYPE[ext]
    src_name = src_path.name

    # 可选重命名：只允许修改文件名主干，扩展名必须与真实文件类型一致（防类型伪装）
    display_name = src_name
    if filename and filename.strip():
        new_name = filename.strip()
        if os.path.basename(new_name) != new_name:
            raise HTTPException(status_code=400, detail="文件名不能包含路径分隔符")
        if len(new_name) > 255:
            raise HTTPException(status_code=400, detail="文件名过长（最多 255 字符）")
        if os.path.splitext(new_name)[1].lower() != ext:
            raise HTTPException(
                status_code=400,
                detail=f"文件名扩展名必须为 {ext}，请保留文件类型后缀",
            )
        display_name = new_name

    # 2. 可选按页裁剪（仅 PDF）；裁剪、哈希、入库均置于 try/finally，
    #    保证任何失败路径都会清理临时目录
    use_path = src_path
    want_crop = bool(crop_page_range and crop_page_range.strip())
    try:
        if want_crop:
            if source_type != SourceType.pdf:
                raise HTTPException(
                    status_code=400,
                    detail=f"仅支持 PDF 裁剪，当前文件类型为 {source_type.value}",
                )
            try:
                page_count = pdf_crop.get_pdf_page_count(str(src_path))
                pages = pdf_crop.parse_page_spec(crop_page_range, page_count)
                cropped_path = src_path.with_name(f"{src_path.stem}_cropped{ext}")
                pdf_crop.crop_pdf(str(src_path), pages, str(cropped_path))
                use_path = cropped_path
                logger.info(
                    "PDF 裁剪完成: user_id=%s, filename=%s, pages=%d/%d",
                    current_user.id, display_name, len(pages), page_count,
                )
            except ValueError as e:
                raise HTTPException(status_code=400, detail=f"裁剪失败: {e}")

        # 计算待存储文件的 SHA-256（裁剪版或原文件）
        sha256 = hashlib.sha256()
        with open(use_path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                sha256.update(chunk)
        file_hash = sha256.hexdigest()
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

        # 3. 复用核心流程：配额校验 → 入库 → 保存 → 触发转换
        return await _do_upload(
            db, current_user, str(use_path), display_name, source_type,
            file_hash, content_type,
            backend, folder_id, project_ids, note_role, linked_material_ids,
        )
    finally:
        # 4. 无论成功失败都清理临时目录（本地模式下 use_path 已被移动/删除）
        shutil.rmtree(temp_dir, ignore_errors=True)


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
