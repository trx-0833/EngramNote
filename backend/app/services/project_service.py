"""
项目服务模块

本模块提供项目的创建、查询、详情、更新和删除等业务逻辑。
项目为「纯标签归属」，通过 note_projects 多对多关联表标记笔记归属，
不再作为 Vault 第一层目录（所有笔记统一落在收件箱 {user_id}/inbox）。

主要职责：
- 创建项目（纯标签，不再生成 slug、不创建物理目录）
- 查询用户的项目列表（含笔记数量）
- 获取项目详情（包含关联笔记列表）
- 更新项目（名称/描述，不影响物理路径）
- 删除项目（只删标签关联，不删除笔记与物理文件）
- 扫描收件箱导入新笔记并打标签 / 批量添加、移出标签

设计决策：
- 项目为纯标签，一篇笔记可属于多个项目（多对多）
- 删除项目只移除标签，文件与笔记全部保留
- 列表按 created_at 降序排列
"""

import logging
import os
import uuid
from typing import Dict, List, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.note import Note, NoteRole, NoteStatus, SourceType
from ..models.note_project import NoteProject
from ..models.project import Project
from ..services.vault_path import (
    ALLOWED_EXTS,
    EXT_TO_SOURCE_TYPE,
    inbox_prefix,
)
from ..services.storage_service import list_source_files
from ..services.vault_meta import write_note_meta, write_project_meta

logger = logging.getLogger(__name__)


async def _write_all_project_meta(db: AsyncSession, user_id: str) -> None:
    """将用户全部项目（标签）清单写穿到用户级 projects.json（镜像非权威，失败不影响主流程）"""
    stmt = select(Project).where(Project.user_id == user_id)
    result = await db.execute(stmt)
    write_project_meta(user_id, list(result.scalars().all()))


async def build_response(project: Project, db: AsyncSession) -> Dict:
    """构建项目响应字典（附带 note_count，基于标签关联表统计）"""
    count_stmt = select(func.count(NoteProject.id)).where(
        NoteProject.project_id == project.id,
        NoteProject.user_id == project.user_id,
    )
    count_result = await db.execute(count_stmt)
    note_count = count_result.scalar() or 0
    return {
        "id": project.id,
        "user_id": project.user_id,
        "name": project.name,
        "description": project.description,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "note_count": note_count,
    }


async def create_project(
    db: AsyncSession,
    user_id: str,
    name: str,
    description: Optional[str] = None,
) -> Project:
    """
    创建项目（纯标签）

    项目不再生成 slug、不创建物理目录；笔记通过 note_projects 关联表打标签。

    Args:
        db: 异步数据库会话
        user_id: 用户 ID
        name: 项目名称
        description: 项目描述，可选

    Returns:
        Project: 新创建的项目对象
    """
    project = Project(
        user_id=user_id,
        name=name,
        description=description,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)

    # 写穿用户级项目清单 output/meta/projects.json，供脱离 DB 浏览时识别标签
    await _write_all_project_meta(db, user_id)

    logger.info("项目创建成功: user_id=%s, project_id=%s, name=%s", user_id, project.id, name)
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
        .join(NoteProject, NoteProject.note_id == Note.id)
        .where(NoteProject.project_id == project_id, Note.trashed_at.is_(None))
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

    项目为纯标签，名称/描述变更不影响物理路径（统一收件箱）。

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

    # 同步用户级项目清单（显示名变更后，磁盘上 projects.json 保持与 DB 一致）
    await _write_all_project_meta(db, user_id)

    logger.info("项目更新成功: user_id=%s, project_id=%s, name=%s", user_id, project_id, project.name)
    return await build_response(project, db)


async def delete_project(db: AsyncSession, project_id: str, user_id: str) -> Dict:
    """
    删除项目（只删标签）

    项目为纯标签：删除仅移除该项目本身及其笔记关联（note_projects 行），
    不删除任何笔记与物理文件；关联笔记保留，只是不再属于该项目。

    Args:
        db: 异步数据库会话
        project_id: 项目 ID
        user_id: 用户 ID，用于权限校验

    Returns:
        Dict: 操作结果，包含 message 字段

    Raises:
        ValueError: 项目不存在或无权访问
    """
    stmt = select(Project).where(Project.id == project_id, Project.user_id == user_id)
    result = await db.execute(stmt)
    project = result.scalars().first()
    if not project:
        raise ValueError("项目不存在或无权访问")

    # 先删标签关联行（外键未开启时需手动保证子→父删除顺序）
    await db.execute(
        delete(NoteProject).where(NoteProject.project_id == project_id)
    )
    await db.delete(project)
    await db.commit()

    # 同步用户级项目清单（删除标签后镜像与 DB 保持一致）
    await _write_all_project_meta(db, user_id)

    logger.info("项目删除成功: user_id=%s, project_id=%s", user_id, project_id)
    return {"message": "项目已删除"}


async def scan_project_source(db: AsyncSession, project: Project, user_id: str) -> Dict:
    """
    扫描收件箱 source/ 目录，将手动放入的新文件识别为笔记并打上项目标签

    项目为纯标签后不再拥有独立目录，所有文件统一落在 {user_id}/inbox/source/。
    本函数：
    1. 列出 inbox/source/ 下所有文件
    2. 跳过不支持扩展名与已导入（original_file_path 相同）的文件
    3. 为每个新文件创建笔记（不设 project_id），并写入 note_projects 关联标签
    4. 触发异步转换任务

    Args:
        db: 异步数据库会话
        project: 项目对象（用于打标签）
        user_id: 用户 ID

    Returns:
        Dict: 扫描结果统计
    """
    prefix = inbox_prefix(user_id)
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
            note_role=NoteRole.material,
            metadata_={"source": "manual_scan"},
        )
        db.add(note)
        # 打上项目标签（多对多关联表，唯一约束防重复）
        db.add(NoteProject(
            note_id=note.id,
            project_id=project.id,
            user_id=user_id,
        ))
        await db.commit()
        await db.refresh(note)
        note._project_ids = [project.id]
        note._project_names = [project.name]
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
    将笔记批量添加到项目（打标签）

    校验项目归属后，为属于该用户的笔记批量插入 note_projects 关联行。
    一篇笔记可属于多个项目（多对多标签），已存在的标签自动跳过
    （唯一约束兜底防重复）。

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
        # 回收站笔记不可加入项目标签
        notes_stmt = select(Note).where(
            Note.user_id == user_id,
            Note.id.in_(note_ids),
            Note.trashed_at.is_(None),
        )
        notes_result = await db.execute(notes_stmt)
        notes = notes_result.scalars().all()

        # 查询该笔记是否已属于该项目（避免重复标签）
        exist_stmt = select(NoteProject.note_id).where(
            NoteProject.project_id == project_id,
            NoteProject.user_id == user_id,
            NoteProject.note_id.in_(note_ids),
        )
        exist_result = await db.execute(exist_stmt)
        existing = set(exist_result.scalars().all())

        # 统一一次 commit，保证批量操作原子性
        for note in notes:
            if note.id in existing:
                continue
            db.add(NoteProject(
                note_id=note.id,
                project_id=project.id,
                user_id=user_id,
            ))
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
    将笔记从项目中移出（移除标签）

    删除 note_projects 关联行；笔记与物理文件全部保留。

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

    link_stmt = select(NoteProject).where(
        NoteProject.project_id == project_id,
        NoteProject.note_id == note_id,
        NoteProject.user_id == user_id,
    )
    link_result = await db.execute(link_stmt)
    link = link_result.scalars().first()
    if not link:
        raise ValueError("笔记不在该项目中")

    await db.delete(link)
    await db.commit()

    logger.info("项目移出笔记: user_id=%s, project_id=%s, note_id=%s", user_id, project_id, note_id)
    return {"message": "笔记已移出项目", "note_id": note_id}
