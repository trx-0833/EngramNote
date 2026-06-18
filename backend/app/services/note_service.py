"""
笔记业务逻辑模块

本模块封装笔记相关的核心业务逻辑，被 notes.py API 层调用。
包括笔记的列表查询、详情获取、Markdown 内容读取、更新和删除操作。

主要职责：
- 分页查询笔记列表，支持关键词搜索
- 获取笔记详情（含用户权限校验）
- 从对象存储读取原始和清洗后的 Markdown 内容
- 更新笔记标题
- 删除笔记及其关联的存储文件

设计决策：
- 列表查询先计算总数再分页，确保分页信息准确
- Markdown 内容从对象存储实时读取，不缓存到数据库，保证数据一致性
- 删除笔记时静默忽略存储文件删除失败（文件可能已不存在），确保数据库记录能正常删除
- 关键词搜索使用 ilike 实现模糊匹配，不区分大小写
"""

from typing import Optional, Tuple, List

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.note import Note, NoteStatus
from ..models.user import User
from ..schemas.note import NoteUpdateRequest
from ..services.storage_service import (
    delete_file,
    get_object_bytes,
    get_presigned_url,
)
from ..config import get_settings

settings = get_settings()


async def get_notes_list(
    db: AsyncSession,
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    keyword: Optional[str] = None,
    note_status: Optional[NoteStatus] = None,
) -> Tuple[list[Note], int]:
    """
    获取用户的笔记列表（分页）

    查询流程：
    1. 构建基础查询条件（按用户 ID 过滤）
    2. 可选：按标题关键词模糊搜索
    3. 可选：按状态筛选
    4. 计算符合条件的总数
    5. 按创建时间倒序分页查询

    Args:
        db: 异步数据库会话
        user_id: 用户 ID，确保只查询该用户的笔记
        page: 页码，从 1 开始
        page_size: 每页数量
        keyword: 搜索关键词，按标题模糊匹配（可选）
        note_status: 按状态筛选（可选）

    Returns:
        Tuple[list[Note], int]: (笔记列表, 总数)
    """
    # 基础查询：只查询当前用户的笔记
    query = select(Note).where(Note.user_id == user_id)

    # 可选：按状态筛选
    if note_status is not None:
        query = query.where(Note.status == note_status)

    # 关键词搜索：使用 ilike 实现不区分大小写的模糊匹配
    if keyword:
        query = query.where(Note.title.ilike(f"%{keyword}%"))

    # 先计算总数，用于分页信息
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # 分页查询：按创建时间倒序，最新笔记在前
    query = query.order_by(Note.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    notes = list(result.scalars().all())

    return notes, total


async def get_note_detail(db: AsyncSession, note_id: str, user_id: str) -> Optional[Note]:
    """
    获取笔记详情

    同时通过 note_id 和 user_id 查询，确保用户只能访问自己的笔记。

    Args:
        db: 异步数据库会话
        note_id: 笔记 ID
        user_id: 用户 ID，用于权限校验

    Returns:
        Optional[Note]: 找到返回笔记对象，否则返回 None
    """
    result = await db.execute(
        select(Note).where(Note.id == note_id, Note.user_id == user_id)
    )
    return result.scalars().first()


async def get_note_markdown_content(note: Note) -> Optional[str]:
    """
    从对象存储读取笔记的原始 Markdown 内容

    即 Mineru/ASR 转换后未经清洗的 Markdown 文本。

    Args:
        note: 笔记对象

    Returns:
        Optional[str]: Markdown 文本内容，读取失败或路径为空返回 None
    """
    if not note.original_md_path:
        return None
    try:
        data = get_object_bytes(settings.minio_bucket_markdown, note.original_md_path)
        return data.decode("utf-8")
    except Exception:
        # 对象存储读取失败时返回 None，不影响其他数据返回
        return None


async def get_clean_markdown_content(note: Note) -> Optional[str]:
    """
    从对象存储读取清洗后的 Markdown 内容

    即经过 AI 清洗优化后的 Markdown 文本，质量更高。

    Args:
        note: 笔记对象

    Returns:
        Optional[str]: 清洗后的 Markdown 文本内容，读取失败或路径为空返回 None
    """
    if not note.clean_md_path:
        return None
    try:
        data = get_object_bytes(settings.minio_bucket_markdown, note.clean_md_path)
        return data.decode("utf-8")
    except Exception:
        return None


async def update_note(db: AsyncSession, note: Note, req: NoteUpdateRequest) -> Note:
    """
    更新笔记

    目前仅支持修改笔记标题。仅更新请求中明确提供的字段（非 None）。

    Args:
        db: 异步数据库会话
        note: 要更新的笔记对象
        req: 更新请求体，包含需要修改的字段

    Returns:
        Note: 更新后的笔记对象
    """
    if req.title is not None:
        note.title = req.title
    await db.commit()
    await db.refresh(note)
    return note


async def delete_note(db: AsyncSession, note: Note):
    """
    删除笔记及其关联的存储文件

    删除顺序：
    1. 删除对象存储中的原始文件
    2. 删除对象存储中的原始 Markdown 文件
    3. 删除对象存储中的清洗后 Markdown 文件
    4. 删除数据库中的笔记记录

    存储文件删除失败时静默忽略（文件可能已不存在或存储服务不可用），
    确保数据库记录始终能正常删除。

    Args:
        db: 异步数据库会话
        note: 要删除的笔记对象
    """
    # 删除 MinIO 中的原始文件
    if note.original_file_path:
        try:
            delete_file(settings.minio_bucket_original, note.original_file_path)
        except Exception:
            pass  # 文件可能已不存在，忽略删除失败

    # 删除 MinIO 中的原始 Markdown 文件
    if note.original_md_path:
        try:
            delete_file(settings.minio_bucket_markdown, note.original_md_path)
        except Exception:
            pass

    # 删除 MinIO 中的清洗后 Markdown 文件
    if note.clean_md_path:
        try:
            delete_file(settings.minio_bucket_markdown, note.clean_md_path)
        except Exception:
            pass

    # 最后删除数据库记录
    await db.delete(note)
    await db.commit()
