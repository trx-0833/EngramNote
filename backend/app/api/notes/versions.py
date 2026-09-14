"""
笔记版本历史 API（子路由模块）

原 api/notes.py 拆分出的版本历史部分。
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.app_error import (
    NOTE_NOT_FOUND,
    NOTE_VERSION_RESTORE_REJECTED,
    VERSION_CONTENT_UNAVAILABLE,
    VERSION_NOT_FOUND,
    AppError,
)
from ...database import get_db
from ...models.user import User
from ...schemas.note_version import (
    NoteVersionContentResponse,
    NoteVersionDiffResponse,
    NoteVersionListResponse,
    NoteVersionResponse,
    NoteVersionRestoreRequest,
)
from ...api.auth import get_current_user_dependency
from ...services.note_service import get_note_detail
from ...services.version_service import version_service

router = APIRouter()


@router.get("/{note_id}/versions", response_model=NoteVersionListResponse)
async def list_note_versions(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取笔记的版本历史列表

    返回指定笔记的所有版本快照，按版本号倒序排列（最新版本在前）。
    调用前会先校验笔记归属权，确保用户只能查询自己笔记的版本历史。

    Args:
        note_id: 笔记 ID
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        NoteVersionListResponse: 包含版本列表和总数的响应

    Raises:
        HTTPException 404: 笔记不存在或不属于当前用户
    """
    # 校验笔记归属权
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise AppError(NOTE_NOT_FOUND, "笔记不存在", 404)

    # 查询版本历史
    versions = await version_service.list_versions(note_id, current_user.id, db)
    return NoteVersionListResponse(
        versions=[NoteVersionResponse.model_validate(v) for v in versions],
        total=len(versions),
    )


@router.get("/{note_id}/versions/diff", response_model=NoteVersionDiffResponse)
async def diff_note_versions(
    note_id: str,
    v1: int = Query(..., description="旧版本号"),
    v2: int = Query(..., description="新版本号"),
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    对比两个版本的行级 diff

    使用 difflib.ndiff 生成差异，将每行标注为 added / removed / unchanged。
    注意：该路由必须注册在 /{version_number} 路由之前，否则 "diff" 会被
    FastAPI 当作 version_number 进行匹配。

    Args:
        note_id: 笔记 ID
        v1: 旧版本号
        v2: 新版本号
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        NoteVersionDiffResponse: 包含两版本号和 diff 行列表的响应

    Raises:
        HTTPException 404: 笔记不存在或任一版本不存在
    """
    # 校验笔记归属权
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise AppError(NOTE_NOT_FOUND, "笔记不存在", 404)

    # 生成 diff
    try:
        diff_data = await version_service.diff_versions(
            note_id, v1, v2, current_user.id, db
        )
    except ValueError as e:
        # "任一版本不存在"由 version_service 直接抛 AppError(VERSION_NOT_FOUND) 上抛，
        # 不会进这里；能落进 except ValueError 的是内容解码失败
        # （UnicodeDecodeError 是 ValueError 子类），故 code 取"内容不可读"。
        raise AppError(VERSION_CONTENT_UNAVAILABLE, str(e), 404) from e

    return NoteVersionDiffResponse(
        v1_number=diff_data["v1_number"],
        v2_number=diff_data["v2_number"],
        diff_lines=diff_data["diff_lines"],
    )


@router.get("/{note_id}/versions/{version_number}", response_model=NoteVersionContentResponse)
async def get_note_version_content(
    note_id: str,
    version_number: int,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    预览指定版本的 Markdown 内容

    从对象存储中读取指定版本的 Markdown 文本内容。
    该路由注册在 /diff 之后，避免 "diff" 被当作 version_number 匹配。

    Args:
        note_id: 笔记 ID
        version_number: 版本号
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        dict: 包含 content（Markdown 文本）和 version_number 的响应

    Raises:
        HTTPException 404: 笔记不存在或版本不存在
    """
    # 校验笔记归属权
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise AppError(NOTE_NOT_FOUND, "笔记不存在", 404)

    # 读取版本内容
    try:
        content = await version_service.get_version_content(
            note_id, version_number, current_user.id, db
        )
    except ValueError as e:
        # 同 diff：版本缺失走 AppError(VERSION_NOT_FOUND)，此处只剩解码失败
        raise AppError(VERSION_CONTENT_UNAVAILABLE, str(e), 404) from e

    return {"content": content, "version_number": version_number}


@router.post("/{note_id}/versions/{version_number}/restore", response_model=NoteVersionResponse)
async def restore_note_version(
    note_id: str,
    version_number: int,
    req: NoteVersionRestoreRequest = None,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    恢复指定历史版本为当前内容

    流程：
    1. 先为笔记当前内容创建一个新版本快照（USER_EDIT 来源）
    2. 用目标版本内容覆盖当前 Markdown 文件
    3. 返回新创建的快照版本信息

    Args:
        note_id: 笔记 ID
        version_number: 要恢复的目标版本号
        req: 恢复请求体（含可选的 confirm 字段，预留用于二次确认）
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        NoteVersionResponse: 恢复前为当前内容创建的新版本快照信息

    Raises:
        HTTPException 404: 笔记或目标版本不存在
        HTTPException 400: 笔记无可写入的 Markdown 路径
    """
    # 校验笔记归属权
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise AppError(NOTE_NOT_FOUND, "笔记不存在", 404)

    # 恢复版本
    try:
        new_version = await version_service.restore_version(
            note_id, version_number, current_user.id, db
        )
    except ValueError as e:
        # 区分"版本不存在"（404）和其余拒绝原因（400）：保持原有的文案判据与状态码，
        # 只把两个出口固定成不同的 code。注意 400 这一支有三种原因
        # （笔记不存在 / 无可写 Markdown 路径 / 当前内容读取失败），
        # 用一个 code 概括，详情仍在文案里。
        message = str(e)
        if "不存在" in message and "版本" in message:
            raise AppError(VERSION_NOT_FOUND, message, 404) from e
        raise AppError(NOTE_VERSION_RESTORE_REJECTED, message, 400) from e

    return NoteVersionResponse.model_validate(new_version)