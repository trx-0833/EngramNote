"""
文件夹服务模块

本模块提供文件夹的创建、查询、详情和删除等业务逻辑。
文件夹用于按日期组织用户上传的学习资料。

主要职责：
- 创建文件夹（自动设置默认日期为今天）
- 查询用户最近 N 天的文件夹列表
- 获取文件夹详情（包含笔记列表和笔记数量）
- 删除文件夹（仅允许删除空文件夹）

设计决策：
- 文件夹日期默认为当天，用户可自定义
- 删除文件夹前检查是否为空，防止误删含资料的文件夹
- 查询结果按 folder_date 降序排列，最近的文件夹在前
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.folder import Folder
from ..models.note import Note

logger = logging.getLogger(__name__)


async def create_folder(
    user_id: str,
    name: str,
    description: Optional[str],
    folder_date: Optional[datetime],
    db: AsyncSession,
) -> Folder:
    """
    创建文件夹

    Args:
        user_id: 用户 ID
        name: 文件夹名称
        description: 文件夹描述，可选
        folder_date: 文件夹日期，可选，默认为今天
        db: 异步数据库会话

    Returns:
        Folder: 新创建的文件夹对象
    """
    # folder_date 默认为今天
    if folder_date is None:
        folder_date = datetime.now(timezone.utc)

    folder = Folder(
        user_id=user_id,
        name=name,
        description=description,
        folder_date=folder_date,
    )
    db.add(folder)
    await db.commit()
    await db.refresh(folder)

    logger.info("文件夹创建成功: user_id=%s, folder_id=%s, name=%s", user_id, folder.id, name)
    return folder


async def get_folders(
    user_id: str,
    db: AsyncSession,
    days: int = 7,
) -> List[Dict]:
    """
    获取用户最近 N 天的文件夹列表

    每个文件夹附带 note_count（笔记数量），按 folder_date 降序排列。

    Args:
        user_id: 用户 ID
        db: 异步数据库会话
        days: 查询最近多少天的文件夹，默认 7 天

    Returns:
        List[Dict]: 文件夹列表，每个包含文件夹信息和 note_count
    """
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)

    # 查询文件夹及其笔记数量（F-08 修复：join 条件叠加 user_id，防止他人笔记计入计数；
    # 回收站笔记已"移出"文件夹，不计入计数）
    stmt = (
        select(
            Folder,
            func.count(Note.id).label("note_count"),
        )
        .outerjoin(
            Note,
            (Note.folder_id == Folder.id)
            & (Note.user_id == user_id)
            & (Note.trashed_at.is_(None)),
        )
        .where(Folder.user_id == user_id, Folder.folder_date >= cutoff_date)
        .group_by(Folder.id)
        .order_by(Folder.folder_date.desc())
    )

    result = await db.execute(stmt)
    rows = result.all()

    folders = []
    for folder, note_count in rows:
        folder_dict = {
            "id": folder.id,
            "user_id": folder.user_id,
            "name": folder.name,
            "description": folder.description,
            "folder_date": folder.folder_date,
            "created_at": folder.created_at,
            "note_count": note_count,
        }
        folders.append(folder_dict)

    return folders


async def get_folder_detail(
    folder_id: str,
    user_id: str,
    db: AsyncSession,
) -> Optional[Dict]:
    """
    获取文件夹详情（包含笔记列表）

    Args:
        folder_id: 文件夹 ID
        user_id: 用户 ID，用于权限校验
        db: 异步数据库会话

    Returns:
        Optional[Dict]: 文件夹详情，包含笔记列表和 note_count；
                        如果文件夹不存在或不属于当前用户，返回 None
    """
    # 查询文件夹
    stmt = select(Folder).where(Folder.id == folder_id, Folder.user_id == user_id)
    result = await db.execute(stmt)
    folder = result.scalars().first()

    if not folder:
        return None

    # 查询文件夹内的笔记（F-08 修复：叠加 user_id 过滤，防止跨用户笔记混入；
    # 回收站笔记不显示）
    notes_stmt = (
        select(Note)
        .where(
            Note.folder_id == folder_id,
            Note.user_id == user_id,
            Note.trashed_at.is_(None),
        )
        .order_by(Note.created_at.desc())
    )
    notes_result = await db.execute(notes_stmt)
    notes = notes_result.scalars().all()

    return {
        "id": folder.id,
        "user_id": folder.user_id,
        "name": folder.name,
        "description": folder.description,
        "folder_date": folder.folder_date,
        "created_at": folder.created_at,
        "note_count": len(notes),
        "notes": [
            {
                "id": note.id,
                "title": note.title,
                "source_type": note.source_type,
                "status": note.status,
                "file_size": note.file_size,
                "created_at": note.created_at,
            }
            for note in notes
        ],
    }


async def update_folder(
    folder_id: str,
    user_id: str,
    name: Optional[str],
    db: AsyncSession,
) -> Dict:
    """
    更新文件夹信息（当前仅支持重命名）

    Args:
        folder_id: 文件夹 ID
        user_id: 用户 ID，用于权限校验
        name: 新文件夹名称；为 None 时不变更
        db: 异步数据库会话

    Returns:
        Dict: 更新后的文件夹信息，包含 note_count

    Raises:
        ValueError: 文件夹不存在或不属于当前用户
    """
    stmt = select(Folder).where(Folder.id == folder_id, Folder.user_id == user_id)
    result = await db.execute(stmt)
    folder = result.scalars().first()

    if not folder:
        raise ValueError("文件夹不存在或无权访问")

    if name is not None and name != folder.name:
        folder.name = name
        await db.commit()
        await db.refresh(folder)
        logger.info("文件夹重命名成功: user_id=%s, folder_id=%s, name=%s", user_id, folder_id, name)

    # 查询笔记数量以保持响应结构一致（F-08 修复：叠加 user_id 过滤；回收站笔记不计入）
    count_stmt = select(func.count(Note.id)).where(
        Note.folder_id == folder_id,
        Note.user_id == user_id,
        Note.trashed_at.is_(None),
    )
    count_result = await db.execute(count_stmt)
    note_count = count_result.scalar() or 0

    return {
        "id": folder.id,
        "user_id": folder.user_id,
        "name": folder.name,
        "description": folder.description,
        "folder_date": folder.folder_date,
        "created_at": folder.created_at,
        "note_count": note_count,
    }


async def delete_folder(
    folder_id: str,
    user_id: str,
    db: AsyncSession,
) -> Dict:
    """
    删除文件夹

    仅允许删除空文件夹（不包含任何笔记的文件夹）。

    Args:
        folder_id: 文件夹 ID
        user_id: 用户 ID，用于权限校验
        db: 异步数据库会话

    Returns:
        Dict: 操作结果，包含 message 字段

    Raises:
        ValueError: 文件夹不存在、不属于当前用户或文件夹非空
    """
    # 查询文件夹
    stmt = select(Folder).where(Folder.id == folder_id, Folder.user_id == user_id)
    result = await db.execute(stmt)
    folder = result.scalars().first()

    if not folder:
        raise ValueError("文件夹不存在或无权访问")

    # 检查文件夹是否为空（F-08 修复：叠加 user_id 过滤；
    # D3 决策：仅含回收站笔记的文件夹视为空、可删除）
    count_stmt = select(func.count(Note.id)).where(
        Note.folder_id == folder_id,
        Note.user_id == user_id,
        Note.trashed_at.is_(None),
    )
    count_result = await db.execute(count_stmt)
    note_count = count_result.scalar()

    if note_count and note_count > 0:
        raise ValueError(f"文件夹非空，包含 {note_count} 个笔记，请先移出或删除笔记")

    # 删除文件夹
    await db.delete(folder)
    await db.commit()

    logger.info("文件夹删除成功: user_id=%s, folder_id=%s", user_id, folder_id)
    return {"message": "文件夹已删除"}
