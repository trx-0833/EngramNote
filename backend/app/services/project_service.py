"""
项目服务模块

本模块提供项目的创建、查询、详情、更新和删除等业务逻辑。
项目是「项目隔离 + 状态旁载」Vault 结构的项目层，用于按主题/任务组织资料。

主要职责：
- 创建项目（自动生成每用户唯一的 slug 作为 Vault 目录名）
- 查询用户的项目列表（含笔记数量）
- 获取项目详情（包含笔记列表）
- 更新项目（仅改显示名/描述，slug 不可变避免物理搬移文件）
- 删除项目（仅允许删除空项目）
- 向项目批量添加/移出笔记（逻辑移动，物理文件路径不变）

设计决策：
- slug 创建后不可变，作为存储路径关键段
- 删除项目前检查是否为空，防止误删含资料的目录
- 列表按 created_at 降序排列
"""

import logging
import os
import uuid
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.note import Note, NoteRole, NoteStatus, SourceType
from ..models.project import Project
from ..services.vault_path import (
    ALLOWED_EXTS,
    EXT_TO_SOURCE_TYPE,
    project_prefix,
    sanitize_slug,
)
from ..services.storage_service import ensure_project_dirs, list_source_files, remove_project_dir
from ..services.vault_meta import write_note_meta

logger = logging.getLogger(__name__)


async def build_response(project: Project, db: AsyncSession) -> Dict:
    """构建项目响应字典（附带 note_count）"""
    count_stmt = select(func.count(Note.id)).where(Note.project_id == project.id)
    count_result = await db.execute(count_stmt)
    note_count = count_result.scalar() or 0
    return {
        "id": project.id,
        "user_id": project.user_id,
        "name": project.name,
        "slug": project.slug,
        "description": project.description,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "note_count": note_count,
        "vault_path": project_prefix(project.user_id, project.slug),
    }


async def create_project(
    db: AsyncSession,
    user_id: str,
    name: str,
    description: Optional[str] = None,
    slug: Optional[str] = None,
) -> Project:
    """
    创建项目

    slug 由项目名清洗生成（可显式指定），冲突时自动追加 -2/-3 后缀保证每用户唯一。

    Args:
        db: 异步数据库会话
        user_id: 用户 ID
        name: 项目名称
        description: 项目描述，可选
        slug: 显式指定 slug（可选），如默认项目固定为 "default"

    Returns:
        Project: 新创建的项目对象
    """
    base_slug = slug if slug else sanitize_slug(name)
    slug = base_slug
    counter = 2
    while True:
        stmt = select(Project).where(Project.user_id == user_id, Project.slug == slug)
        result = await db.execute(stmt)
        if result.scalars().first() is None:
            break
        slug = f"{base_slug}-{counter}"
        counter += 1

    project = Project(
        user_id=user_id,
        name=name,
        slug=slug,
        description=description,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)

    # 在磁盘/对象存储中预建 Vault 目录树，让用户可直接浏览并放入文件
    try:
        ensure_project_dirs(project_prefix(user_id, slug))
    except Exception as e:
        logger.warning("项目目录树创建失败（不影响项目创建）: %s", e, exc_info=True)

    logger.info("项目创建成功: user_id=%s, project_id=%s, name=%s, slug=%s", user_id, project.id, name, slug)
    return project


async def list_projects(db: AsyncSession, user_id: str) -> List[Dict]:
    """
    获取用户的项目列表（含笔记数量），按创建时间降序

    Args:
        db: 异步数据库会话
        user_id: 用户 ID

    Returns:
        List[Dict]: 项目列表
    """
    stmt = select(Project).where(Project.user_id == user_id).order_by(Project.created_at.desc())
    result = await db.execute(stmt)
    projects = result.scalars().all()
    return [await build_response(p, db) for p in projects]


async def get_project(db: AsyncSession, project_id: str, user_id: str) -> Optional[Dict]:
    """
    获取项目详情（包含笔记列表）

    Args:
        db: 异步数据库会话
        project_id: 项目 ID
        user_id: 用户 ID，用于权限校验

    Returns:
        Optional[Dict]: 项目详情；不存在或无权访问时返回 None
    """
    stmt = select(Project).where(Project.id == project_id, Project.user_id == user_id)
    result = await db.execute(stmt)
    project = result.scalars().first()
    if not project:
        return None

    base = await build_response(project, db)

    notes_stmt = (
        select(Note)
        .where(Note.project_id == project_id)
        .order_by(Note.created_at.desc())
    )
    notes_result = await db.execute(notes_stmt)
    notes = notes_result.scalars().all()

    base["notes"] = [
        {
            "id": note.id,
            "title": note.title,
            "status": note.status.value if note.status else None,
            "source_type": note.source_type.value if note.source_type else None,
            "created_at": note.created_at,
        }
        for note in notes
    ]
    return base


async def update_project(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> Dict:
    """
    更新项目信息

    slug 不可变（Vault 目录名），仅更新显示名与描述。

    Args:
        db: 异步数据库会话
        project_id: 项目 ID
        user_id: 用户 ID，用于权限校验
        name: 新名称，None 表示不变更
        description: 新描述，None 表示不变更

    Returns:
        Dict: 更新后的项目信息

    Raises:
        ValueError: 项目不存在或无权访问
    """
    stmt = select(Project).where(Project.id == project_id, Project.user_id == user_id)
    result = await db.execute(stmt)
    project = result.scalars().first()
    if not project:
        raise ValueError("项目不存在或无权访问")

    if name is not None and name != project.name:
        project.name = name
    if description is not None:
        project.description = description
    await db.commit()
    await db.refresh(project)

    logger.info("项目更新成功: user_id=%s, project_id=%s, name=%s", user_id, project_id, project.name)
    return await build_response(project, db)


async def delete_project(db: AsyncSession, project_id: str, user_id: str) -> Dict:
    """
    删除项目

    仅允许删除空项目（不包含任何笔记）。

    Args:
        db: 异步数据库会话
        project_id: 项目 ID
        user_id: 用户 ID，用于权限校验

    Returns:
        Dict: 操作结果，包含 message 字段

    Raises:
        ValueError: 项目不存在、无权访问或项目非空
    """
    stmt = select(Project).where(Project.id == project_id, Project.user_id == user_id)
    result = await db.execute(stmt)
    project = result.scalars().first()
    if not project:
        raise ValueError("项目不存在或无权访问")

    count_stmt = select(func.count(Note.id)).where(Note.project_id == project_id)
    count_result = await db.execute(count_stmt)
    note_count = count_result.scalar() or 0
    if note_count > 0:
        raise ValueError(f"项目非空，包含 {note_count} 个笔记，请先移出或删除笔记")

    await db.delete(project)
    await db.commit()

    # 清理磁盘/对象存储中的项目目录树（删除项目后不再保留物理文件）
    try:
        remove_project_dir(project_prefix(user_id, project.slug))
    except Exception as e:
        logger.warning("项目目录树清理失败（不影响项目删除）: %s", e, exc_info=True)

    logger.info("项目删除成功: user_id=%s, project_id=%s", user_id, project_id)
    return {"message": "项目已删除"}


async def scan_project_source(db: AsyncSession, project: Project, user_id: str) -> Dict:
    """
    扫描项目的 source/ 目录，将手动放入的新文件识别为笔记

    用户将文件直接放入 Vault 目录树的项目 source/ 后，调用本函数：
    1. 列出 source/ 下所有文件
    2. 跳过不支持扩展名与已导入（original_file_path 相同）的文件
    3. 为每个新文件创建笔记并触发转换任务

    Args:
        db: 异步数据库会话
        project: 项目对象
        user_id: 用户 ID

    Returns:
        Dict: 扫描结果统计
    """
    prefix = project_prefix(user_id, project.slug)
    files = list_source_files(prefix)

    imported, skipped, unsupported = [], [], []
    for rel_path, size in files:
        ext = "." + rel_path.rsplit(".", 1)[-1].lower() if "." in rel_path else ""
        if ext not in ALLOWED_EXTS:
            unsupported.append({"path": rel_path, "reason": f"不支持的文件格式: {ext}"})
            continue

        object_name = f"{prefix}/source/{rel_path}"

        # 跳过已导入（同名原始文件已存在笔记）
        exist_stmt = select(Note).where(Note.user_id == user_id, Note.original_file_path == object_name)
        exist_result = await db.execute(exist_stmt)
        if exist_result.scalars().first() is not None:
            skipped.append({"path": rel_path, "reason": "已导入"})
            continue

        # 命名关联法则：md 名与 source 名同主干（仅扩展名不同）
        source_type = SourceType(EXT_TO_SOURCE_TYPE[ext])

        note = Note(
            id=str(uuid.uuid4()),
            user_id=user_id,
            title=os.path.splitext(rel_path.split("/")[-1])[0],
            source_type=source_type,
            original_file_path=object_name,
            status=NoteStatus.converting,
            file_size=size,
            project_id=project.id,
            note_role=NoteRole.material,
            metadata_={"source": "manual_scan"},
        )
        db.add(note)
        await db.commit()
        await db.refresh(note)
        write_note_meta(note)

        # 触发异步转换任务（Celery 不可用时标记失败，文件已就位可重试）
        try:
            from ..tasks.convert_tasks import convert_document_task
            convert_document_task.delay(note.id, object_name, source_type.value)
        except Exception as e:
            note.status = NoteStatus.failed
            note.error_message = f"转换任务提交失败: {str(e)}"
            await db.commit()
            await db.refresh(note)
            write_note_meta(note)

        imported.append(
            {
                "id": note.id,
                "title": note.title,
                "status": note.status.value,
                "source_type": note.source_type.value,
                "path": rel_path,
            }
        )
        logger.info("扫描导入: user_id=%s, project_id=%s, path=%s, note_id=%s", user_id, project.id, rel_path, note.id)

    return {
        "project_id": project.id,
        "project_name": project.name,
        "scanned": len(files),
        "imported": len(imported),
        "skipped": len(skipped),
        "unsupported": len(unsupported),
        "imported_notes": imported,
        "skipped_details": skipped,
        "unsupported_details": unsupported,
    }


async def add_notes_to_project(
    db: AsyncSession, project_id: str, user_id: str, note_ids: List[str]
) -> Dict:
    """
    将笔记批量添加到项目

    校验项目归属后，把属于该用户且 id 在 note_ids 中的笔记的 project_id
    更新为目标项目。

    注意：被添加的笔记若原本属于其他项目，这里直接改 project_id
    （逻辑移动，物理文件路径不变）——这是有意取舍：笔记文件仍留在原
    Vault 目录中，仅变更项目归属关系，避免搬移磁盘文件。

    Args:
        db: 异步数据库会话
        project_id: 目标项目 ID
        user_id: 用户 ID，用于权限校验
        note_ids: 要添加的笔记 ID 列表（允许为空列表，此时不产生变更）

    Returns:
        Dict: 添加结果统计（project_id / added / not_found）

    Raises:
        ValueError: 项目不存在或无权访问
    """
    stmt = select(Project).where(Project.id == project_id, Project.user_id == user_id)
    result = await db.execute(stmt)
    project = result.scalars().first()
    if not project:
        raise ValueError("项目不存在或无权访问")

    added = 0
    if note_ids:
        notes_stmt = select(Note).where(Note.user_id == user_id, Note.id.in_(note_ids))
        notes_result = await db.execute(notes_stmt)
        notes = notes_result.scalars().all()
        # 统一一次 commit，保证批量操作原子性
        for note in notes:
            note.project_id = project.id
            added += 1
        await db.commit()
        logger.info("项目添加笔记: user_id=%s, project_id=%s, added=%s, requested=%s", user_id, project_id, added, len(note_ids))

    return {
        "project_id": project.id,
        "added": added,
        "not_found": len(note_ids) - added,
    }


async def remove_note_from_project(
    db: AsyncSession, project_id: str, note_id: str, user_id: str
) -> Dict:
    """
    将笔记从项目中移出

    置 note.project_id = None（逻辑移出，物理文件路径不变，笔记文件
    仍留在原 Vault 目录中）。

    Args:
        db: 异步数据库会话
        project_id: 项目 ID
        note_id: 要移出的笔记 ID
        user_id: 用户 ID，用于权限校验

    Returns:
        Dict: 操作结果（message / note_id）

    Raises:
        ValueError: 项目不存在或无权访问，或笔记不在该项目中
    """
    stmt = select(Project).where(Project.id == project_id, Project.user_id == user_id)
    result = await db.execute(stmt)
    project = result.scalars().first()
    if not project:
        raise ValueError("项目不存在或无权访问")

    note_stmt = select(Note).where(
        Note.id == note_id,
        Note.user_id == user_id,
        Note.project_id == project_id,
    )
    note_result = await db.execute(note_stmt)
    note = note_result.scalars().first()
    if not note:
        raise ValueError("笔记不在该项目中")

    note.project_id = None
    await db.commit()
    await db.refresh(note)

    logger.info("项目移出笔记: user_id=%s, project_id=%s, note_id=%s", user_id, project_id, note_id)
    return {"message": "笔记已移出项目", "note_id": note.id}
