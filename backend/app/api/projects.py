"""
项目 API 模块

本模块提供项目的创建、查询、详情、更新和删除接口。
项目是「项目隔离 + 状态旁载」Vault 结构的项目层，用于按主题/任务组织资料。

主要职责：
- 创建项目（POST /api/projects）
- 获取项目列表（GET /api/projects），含笔记数量
- 获取项目详情（GET /api/projects/{project_id}），含笔记列表
- 重命名/更新项目（PATCH /api/projects/{project_id}）
- 删除项目（DELETE /api/projects/{project_id}），仅允许删除空项目

设计决策：
- 所有接口通过 get_current_user_dependency 确保用户已认证
- 项目查询自动过滤 user_id，确保用户只能访问自己的数据
- slug 创建后不可变，重命名项目只改显示名，避免物理搬移 Vault 目录
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.project import Project
from ..models.user import User
from ..schemas.project import (
    ProjectCreate,
    ProjectDetailResponse,
    ProjectNotesAddRequest,
    ProjectResponse,
    ProjectUpdate,
    ScanImportResponse,
)
from ..api.auth import get_current_user_dependency
from ..services import project_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    req: ProjectCreate,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    创建项目（纯标签）

    项目为纯标签，不再生成 slug、不创建物理目录。

    Args:
        req: 项目创建请求，包含 name、description
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        ProjectResponse: 新创建的项目信息
    """
    project = await project_service.create_project(
        db, current_user.id, req.name, req.description
    )
    return await project_service.build_response(project, db)


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取当前用户的项目列表（含笔记数量）

    Args:
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        List[ProjectResponse]: 项目列表
    """
    return await project_service.list_projects(db, current_user.id)


@router.get("/{project_id}", response_model=ProjectDetailResponse)
async def get_project(
    project_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取项目详情（包含笔记列表）

    Args:
        project_id: 项目 ID
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        ProjectDetailResponse: 项目详情

    Raises:
        HTTPException: 项目不存在或无权访问
    """
    detail = await project_service.get_project(db, project_id, current_user.id)
    if not detail:
        raise HTTPException(status_code=404, detail="项目不存在")
    return detail


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    req: ProjectUpdate,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    更新项目信息（标签化后名称/描述可改，不影响物理路径）

    Args:
        project_id: 项目 ID
        req: 项目更新请求
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        ProjectResponse: 更新后的项目信息

    Raises:
        HTTPException: 项目不存在或无权访问
    """
    try:
        return await project_service.update_project(
            db, project_id, current_user.id, req.name, req.description
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{project_id}")
async def delete_project(
    project_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    删除项目（只删标签）

    删除项目本身及其笔记标签关联；笔记与物理文件全部保留。

    Args:
        project_id: 项目 ID
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        Dict: 操作结果

    Raises:
        HTTPException: 项目不存在或无权访问
    """
    try:
        return await project_service.delete_project(db, project_id, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{project_id}/scan", response_model=ScanImportResponse)
async def scan_project_source(
    project_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    扫描导入：将手动放入收件箱 source/ 目录的新文件识别为笔记并打上项目标签

    项目为纯标签后不再拥有独立目录，所有文件统一落在 {user_id}/inbox/source/。
    用户可把文件直接拷贝到该目录后调用本接口批量导入（每个文件创建一条笔记
    并打上当前项目标签、触发转换任务）。已导入过的文件会被自动跳过。

    Args:
        project_id: 项目 ID
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        ScanImportResponse: 扫描结果统计

    Raises:
        HTTPException: 项目不存在或无权访问
    """
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.user_id == current_user.id)
    )
    project = result.scalars().first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    return await project_service.scan_project_source(db, project, current_user.id)


@router.post("/{project_id}/notes")
async def add_notes(
    project_id: str,
    req: ProjectNotesAddRequest,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    将笔记批量添加到项目（打标签）

    为属于当前用户且 id 在 req.note_ids 中的笔记插入 note_projects 关联行；
    一篇笔记可属于多个项目（多对多标签），已存在的标签自动跳过。

    Args:
        project_id: 项目 ID
        req: 请求体，包含要添加的笔记 ID 列表
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        Dict: 添加结果统计（project_id / added / not_found）

    Raises:
        HTTPException: 项目不存在或无权访问
    """
    try:
        return await project_service.add_notes_to_project(
            db, project_id, current_user.id, req.note_ids
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{project_id}/notes/{note_id}")
async def remove_note(
    project_id: str,
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    将笔记从项目中移出（移除标签）

    删除 note_projects 关联行；笔记与物理文件全部保留。

    Args:
        project_id: 项目 ID
        note_id: 要移出的笔记 ID
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        Dict: 操作结果（message / note_id）

    Raises:
        HTTPException: 项目不存在、无权访问或笔记不在该项目中
    """
    try:
        return await project_service.remove_note_from_project(
            db, project_id, note_id, current_user.id
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
