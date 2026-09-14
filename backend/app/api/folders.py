"""
文件夹 API 模块

本模块提供文件夹的创建、查询、详情和删除接口。
文件夹用于按日期组织用户上传的学习资料。

主要职责：
- 创建文件夹（POST /api/folders）
- 获取文件夹列表（GET /api/folders），支持按天数筛选
- 获取文件夹详情（GET /api/folders/{folder_id}），包含笔记列表
- 重命名文件夹（PATCH /api/folders/{folder_id}），更新文件夹名称
- 删除文件夹（DELETE /api/folders/{folder_id}），仅允许删除空文件夹

设计决策：
- 所有接口通过 get_current_user_dependency 确保用户已认证
- 文件夹查询自动过滤 user_id，确保用户只能访问自己的数据
- 删除文件夹前检查是否为空，防止误删含资料的文件夹
- folder_date 支持传入 ISO 日期字符串，默认为当天
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

import logging

from ..database import get_db
from ..core.app_error import (
    FOLDER_DATE_INVALID,
    FOLDER_NOT_EMPTY,
    FOLDER_NOT_FOUND,
    AppError,
)
from ..models.user import User
from ..schemas.common import MessageResponse
from ..schemas.folder import (
    FolderCreate,
    FolderDetailResponse,
    FolderResponse,
    FolderUpdate,
    NoteInFolder,
)
from ..api.auth import get_current_user_dependency
from ..services.folder_service import (
    create_folder as svc_create_folder,
    delete_folder as svc_delete_folder,
    get_folder_detail as svc_get_folder_detail,
    get_folders as svc_get_folders,
    update_folder as svc_update_folder,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("", response_model=FolderResponse, status_code=status.HTTP_201_CREATED)
async def create_folder(
    req: FolderCreate,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    创建文件夹

    创建一个新的学习资料文件夹，folder_date 默认为今天。

    Args:
        req: 文件夹创建请求，包含 name、description、folder_date
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        FolderResponse: 新创建的文件夹信息
    """
    # 解析 folder_date，默认为今天
    folder_date = None
    if req.folder_date:
        try:
            folder_date = datetime.fromisoformat(req.folder_date).replace(
                hour=0, minute=0, second=0, microsecond=0,
                tzinfo=timezone.utc,
            )
        except ValueError as e:
            raise AppError(
                FOLDER_DATE_INVALID, "无效的日期格式，请使用 ISO 格式如 2024-01-15", 400
            ) from e

    folder = await svc_create_folder(
        user_id=current_user.id,
        name=req.name,
        description=req.description,
        folder_date=folder_date,
        db=db,
    )

    return FolderResponse(
        id=folder.id,
        user_id=folder.user_id,
        name=folder.name,
        description=folder.description,
        folder_date=folder.folder_date,
        created_at=folder.created_at,
        note_count=0,
    )


@router.get("", response_model=list[FolderResponse])
async def list_folders(
    days: int = Query(7, ge=1, le=90, description="查询最近多少天的文件夹"),
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取文件夹列表

    返回当前用户最近 N 天的文件夹列表，按 folder_date 降序排列。

    Args:
        days: 查询最近多少天的文件夹，默认 7 天
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        List[FolderResponse]: 文件夹列表
    """
    folders = await svc_get_folders(
        user_id=current_user.id,
        db=db,
        days=days,
    )
    return [FolderResponse(**f) for f in folders]


@router.get("/{folder_id}", response_model=FolderDetailResponse)
async def get_folder(
    folder_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取文件夹详情

    返回文件夹信息及其包含的笔记列表。

    Args:
        folder_id: 文件夹 ID
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        FolderDetailResponse: 文件夹详情，包含笔记列表

    Raises:
        HTTPException 404: 文件夹不存在或不属于当前用户
    """
    detail = await svc_get_folder_detail(
        folder_id=folder_id,
        user_id=current_user.id,
        db=db,
    )
    if not detail:
        raise AppError(FOLDER_NOT_FOUND, "文件夹不存在", 404)

    return FolderDetailResponse(
        id=detail["id"],
        user_id=detail["user_id"],
        name=detail["name"],
        description=detail["description"],
        folder_date=detail["folder_date"],
        created_at=detail["created_at"],
        note_count=detail["note_count"],
        notes=[NoteInFolder.model_validate(n) for n in detail["notes"]],
    )


@router.patch("/{folder_id}", response_model=FolderResponse)
async def update_folder(
    folder_id: str,
    req: FolderUpdate,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    更新文件夹信息（当前仅支持重命名）

    Args:
        folder_id: 文件夹 ID
        req: 更新请求，包含可选的 name 字段
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        FolderResponse: 更新后的文件夹信息

    Raises:
        HTTPException 404: 文件夹不存在或不属于当前用户
    """
    try:
        updated = await svc_update_folder(
            folder_id=folder_id,
            user_id=current_user.id,
            name=req.name,
            db=db,
        )
    except ValueError as e:
        raise AppError(FOLDER_NOT_FOUND, str(e), 404) from e

    return FolderResponse(**updated)


@router.delete("/{folder_id}", response_model=MessageResponse, status_code=status.HTTP_200_OK)
async def delete_folder(
    folder_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    删除文件夹

    仅允许删除空文件夹（不包含任何笔记的文件夹）。

    Args:
        folder_id: 文件夹 ID
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        Dict: 操作结果，包含 message 字段

    Raises:
        HTTPException 404: 文件夹不存在或不属于当前用户
        HTTPException 400: 文件夹非空，不允许删除
    """
    try:
        result = await svc_delete_folder(
            folder_id=folder_id,
            user_id=current_user.id,
            db=db,
        )
        return result
    except ValueError as e:
        # 两种失败原因用不同的 error_code：服务层靠文案区分（"不存在" / "非空"），
        # 状态码 404 / 400 也据此分流 —— 迁移只固定 code，不改这个既有判据。
        if "不存在" in str(e):
            raise AppError(FOLDER_NOT_FOUND, str(e), 404) from e
        raise AppError(FOLDER_NOT_EMPTY, str(e), 400) from e
