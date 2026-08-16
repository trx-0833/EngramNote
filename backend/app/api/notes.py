"""
笔记 CRUD API 模块

本模块提供笔记的增删改查 HTTP 接口，所有接口均需要用户认证。
笔记是系统的核心资源，用户只能访问自己创建的笔记。

主要职责：
- 获取笔记列表（GET /api/notes），支持分页和关键词搜索
- 获取笔记详情（GET /api/notes/{note_id}），包含 Markdown 内容
- 更新笔记（PUT /api/notes/{note_id}），目前仅支持修改标题
- 删除笔记（DELETE /api/notes/{note_id}），同时删除关联的存储文件

设计决策：
- 所有接口通过 get_current_user_dependency 确保用户已认证
- 笔记查询自动过滤 user_id，确保用户只能访问自己的数据
- 获取详情时同时读取原始和清洗后的 Markdown 内容
- 删除笔记时同步清理对象存储中的关联文件
"""

import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.note import Note, NoteRole, NoteStatus, SourceType
from ..models.note_project import NoteProject
from ..models.project import Project
from ..models.user import User
from ..schemas.note import (
    NoteContentUpdateRequest,
    NoteDetailResponse,
    NoteListResponse,
    NoteResponse,
    NoteUpdateRequest,
)
from ..schemas.note_material_link import LinkCreateRequest, LinkListResponse
from ..schemas.note_annotation import (
    AnnotationCreateRequest,
    AnnotationResponse,
    AnnotationListResponse,
)
from ..schemas.note_version import (
    NoteVersionResponse,
    NoteVersionListResponse,
    NoteVersionDiffResponse,
    NoteVersionRestoreRequest,
)
from ..services.version_service import version_service
from ..api.auth import get_current_user_dependency
from ..config import get_settings
from ..services import note_service
from ..services.note_service import (
    delete_note,
    get_clean_markdown_content,
    get_note_detail,
    get_note_markdown_content,
    get_notes_list,
    update_note,
)
from ..services.storage_service import _resolve_path, get_presigned_url

settings = get_settings()

router = APIRouter()


def _fill_project_names(resp: NoteResponse, note: Note, mapping: dict) -> None:
    """为响应填充 project_ids/project_names（来自预查询的标签映射）"""
    ids, names = mapping.get(note.id, ([], []))
    resp.project_ids = ids
    resp.project_names = names


async def _load_project_tags(db: AsyncSession, notes) -> dict:
    """批量查询笔记的项目标签，返回 {note_id: (project_ids, project_names)} 映射"""
    note_ids = [n.id for n in notes]
    if not note_ids:
        return {}
    result = await db.execute(
        select(NoteProject.note_id, Project.id, Project.name)
        .join(Project, Project.id == NoteProject.project_id)
        .where(NoteProject.note_id.in_(note_ids))
    )
    mapping = {nid: ([], []) for nid in note_ids}
    for note_id, pid, pname in result.all():
        mapping[note_id][0].append(pid)
        mapping[note_id][1].append(pname)
    return mapping


async def _build_note_response(db: AsyncSession, note: Note) -> NoteResponse:
    """构建单个 NoteResponse（含项目标签数组），视频类型填充 video_url"""
    resp = NoteResponse.model_validate(note)
    tags = await _load_project_tags(db, [note])
    _fill_project_names(resp, note, tags)
    if note.source_type == SourceType.video:
        resp.video_url = f"/api/notes/{note.id}/video"
    return resp


async def _build_note_responses(db: AsyncSession, notes) -> list[NoteResponse]:
    """批量构建 NoteResponse（一次查询所有项目标签，避免 N+1）"""
    tags = await _load_project_tags(db, notes)
    responses = []
    for n in notes:
        resp = NoteResponse.model_validate(n)
        _fill_project_names(resp, n, tags)
        if n.source_type == SourceType.video:
            resp.video_url = f"/api/notes/{n.id}/video"
        responses.append(resp)
    return responses


@router.get("", response_model=NoteListResponse)
async def list_notes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    keyword: Optional[str] = None,
    note_role: Optional[str] = Query(None, description="笔记角色过滤：material 或 personal_note"),
    project_id: Optional[str] = Query(None, description="按项目过滤"),
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取笔记列表

    返回当前用户的笔记列表，支持分页和标题关键词搜索。
    按创建时间倒序排列（最新笔记在前）。

    Args:
        page: 页码，从 1 开始，默认第 1 页
        page_size: 每页数量，默认 20，最大 1000（项目"添加笔记"面板一次拉取候选需要）
        keyword: 搜索关键词，按标题模糊匹配（可选）
        note_role: 笔记角色过滤（可选），material 或 personal_note
        project_id: 按项目过滤（可选）
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        NoteListResponse: 包含笔记列表、总数、分页信息的响应
    """
    if note_role is not None:
        valid_roles = [e.value for e in NoteRole]
        if note_role not in valid_roles:
            raise HTTPException(
                status_code=400,
                detail=f"无效的 note_role 值: {note_role}，有效值为: {', '.join(valid_roles)}",
            )
    notes, total = await get_notes_list(
        db, current_user.id, page, page_size, keyword, note_role=note_role, project_id=project_id
    )
    items = await _build_note_responses(db, notes)
    return NoteListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/archive", response_model=NoteListResponse)
async def list_archived_notes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    note_role: Optional[str] = Query(None, description="笔记角色过滤：material 或 personal_note"),
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取已归档笔记列表

    按创建时间倒序排列，专门展示已归档的笔记。
    """
    if note_role is not None:
        valid_roles = [e.value for e in NoteRole]
        if note_role not in valid_roles:
            raise HTTPException(
                status_code=400,
                detail=f"无效的 note_role 值: {note_role}，有效值为: {', '.join(valid_roles)}",
            )
    notes, total = await get_notes_list(
        db, current_user.id, page, page_size, note_status=NoteStatus.archived, note_role=note_role
    )
    items = await _build_note_responses(db, notes)
    return NoteListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{note_id}", response_model=NoteDetailResponse)
async def get_note(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取笔记详情

    返回笔记的基本信息和 Markdown 内容（包括原始转换结果和清洗后结果）。
    Markdown 内容从对象存储中实时读取，不存储在数据库中。

    Args:
        note_id: 笔记 ID
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        NoteDetailResponse: 包含笔记详情和 Markdown 内容的响应

    Raises:
        HTTPException 404: 笔记不存在或不属于当前用户
    """
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    # 从对象存储中读取 Markdown 内容
    original_md = await get_note_markdown_content(note)
    clean_md = await get_clean_markdown_content(note)

    resp = NoteDetailResponse.model_validate(note)
    resp.original_md_content = original_md
    resp.clean_md_content = clean_md
    tags = await _load_project_tags(db, [note])
    _fill_project_names(resp, note, tags)
    if note.source_type == SourceType.video:
        resp.video_url = f"/api/notes/{note.id}/video"
    return resp


@router.put("/{note_id}", response_model=NoteResponse)
async def update_note_api(
    note_id: str,
    req: NoteUpdateRequest,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    更新笔记

    目前仅支持修改笔记标题。后续可扩展支持更多字段的更新。

    Args:
        note_id: 笔记 ID
        req: 更新请求体，包含需要修改的字段
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        NoteResponse: 更新后的笔记信息

    Raises:
        HTTPException 404: 笔记不存在或不属于当前用户
    """
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    updated = await update_note(db, note, req)
    return await _build_note_response(db, updated)


@router.put("/{note_id}/content", response_model=NoteResponse)
async def update_note_content(
    note_id: str,
    req: NoteContentUpdateRequest,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    更新笔记的 Markdown 内容

    将用户编辑的 Markdown 内容保存到对象存储，覆盖原有文件。
    处理中状态（uploading/converting/cleaning/learning）的笔记不可编辑。
    """
    # 1. 校验笔记归属
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    # 2. 校验处理中状态
    processing_statuses = {
        NoteStatus.uploading, NoteStatus.converting,
        NoteStatus.cleaning, NoteStatus.learning,
    }
    if note.status in processing_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"笔记正在处理中（{note.status.value}），暂不可编辑",
        )

    # 2.1 原始版内容不可编辑（只读），仅允许编辑清洗版
    if req.target == "original":
        raise HTTPException(
            status_code=400,
            detail="原始版内容不可编辑，请切换到清洗版后编辑",
        )

    # 3. 校验内容大小（5MB 限制，按字节数计算）
    content_size = len(req.content.encode("utf-8"))
    if content_size > 5 * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail="内容过大（超过 5MB），请缩短后重试",
        )

    # 4. 保存内容
    success = await note_service.save_note_content(db, note, req.content, req.target)
    if not success:
        raise HTTPException(
            status_code=400,
            detail="目标 Markdown 路径为空，资料可能尚未转换完成",
        )

    # 5. 返回更新后的笔记信息
    return await _build_note_response(db, note)


@router.post("/{note_id}/archive", response_model=NoteResponse)
async def archive_note_api(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    手动归档/取消归档笔记

    切换笔记的归档状态：
    - archived → cleaned（取消归档）
    - cleaned/learning_failed → archived（归档）
    仅 converted/cleaned/learning_failed/archived 状态可操作。

    Args:
        note_id: 笔记 ID
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        NoteResponse: 更新后的笔记信息
    """
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    if note.status not in (
        NoteStatus.archived, NoteStatus.cleaned,
        NoteStatus.learning_failed, NoteStatus.converted,
    ):
        raise HTTPException(
            status_code=400,
            detail=f"当前状态 {note.status.value} 不允许归档/取消归档操作",
        )

    if note.status == NoteStatus.archived:
        # F-26 修复：取消归档恢复原状态语义——
        # converted 笔记归档后取消应回到 converted（从未清洗，不能谎称 cleaned）；
        # 其余（cleaned/learning_failed）回到 cleaned。
        # 判断依据：clean_md_path 是否存在（该笔记是否产出过清洗副本）。
        if note.clean_md_path:
            note.status = NoteStatus.cleaned
        else:
            note.status = NoteStatus.converted
    else:
        note.status = NoteStatus.archived
    note.error_message = None
    await db.commit()
    await db.refresh(note)
    # 归档状态变更同步写穿 meta 镜像
    from ..services.vault_meta import write_note_meta
    write_note_meta(note)
    return await _build_note_response(db, note)


@router.get("/{note_id}/links", response_model=LinkListResponse)
async def get_note_links(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """获取笔记的链接关系"""
    note = await note_service.get_note_detail(db, note_id, current_user.id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    linked_materials = []
    linked_personal_notes = []

    if note.note_role == NoteRole.personal_note:
        # 正向查询：获取关联的资料
        materials = await note_service.get_linked_materials(db, current_user.id, note_id)
        linked_materials = [
            {
                "id": m.id,
                "title": m.title,
                "source_type": m.source_type.value if m.source_type else None,
            }
            for m in materials
        ]
    elif note.note_role == NoteRole.material:
        # 反向查询：获取引用该资料的笔记
        personal_notes = await note_service.get_linked_personal_notes(db, current_user.id, note_id)
        linked_personal_notes = [{"id": n.id, "title": n.title} for n in personal_notes]

    return {
        "personal_note_id": note_id,
        "linked_materials": linked_materials,
        "linked_personal_notes": linked_personal_notes,
    }


@router.put("/{note_id}/links")
async def update_note_links(
    note_id: str,
    request: LinkCreateRequest,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """更新笔记-资料链接关系"""
    note = await note_service.get_note_detail(db, note_id, current_user.id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    # 仅 personal_note 可设置正向链接
    if note.note_role != NoteRole.personal_note:
        raise HTTPException(status_code=400, detail="仅个人笔记可设置关联资料")

    # 校验所有 material_note_ids 归属和角色
    if request.material_note_ids:
        for material_id in request.material_note_ids:
            material = await note_service.get_note_detail(db, material_id, current_user.id)
            if not material:
                raise HTTPException(status_code=404, detail=f"资料 {material_id} 不存在")
            if material.note_role != NoteRole.material:
                raise HTTPException(status_code=400, detail=f"笔记 {material_id} 不是学习资料")

    # 更新链接
    changed = await note_service.update_note_material_links(
        db, current_user.id, note_id, request.material_note_ids
    )

    # 如果链接变化，标记该笔记的 quiz 缓存为 stale
    if changed:
        from ..models.assessment import AssessmentResult
        result = await db.execute(
            select(AssessmentResult).where(
                AssessmentResult.user_id == current_user.id,
                AssessmentResult.mode == "quiz",
                AssessmentResult.is_stale == False,
            )
        )
        for ar in result.scalars().all():
            if note_id in (ar.personal_note_ids or []) or note_id in (ar.material_note_ids or []):
                ar.is_stale = True
        await db.commit()

    return {"changed": changed}


@router.get("/{note_id}/annotations", response_model=AnnotationListResponse)
async def get_annotations(
    note_id: str,
    view_mode: Optional[str] = Query(None, description="视图模式：original 或 clean"),
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """获取笔记批注列表"""
    note = await note_service.get_note_detail(db, note_id, current_user.id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    annotations = await note_service.get_annotations(db, note_id, current_user.id, view_mode)
    return {"annotations": annotations}


@router.post("/{note_id}/annotations", response_model=AnnotationResponse, status_code=201)
async def create_annotation(
    note_id: str,
    request: AnnotationCreateRequest,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """创建批注"""
    note = await note_service.get_note_detail(db, note_id, current_user.id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    # 校验 type 和 view_mode 合法值
    if request.type not in ("highlight", "underline"):
        raise HTTPException(status_code=400, detail="type 必须为 highlight 或 underline")
    if request.view_mode not in ("original", "clean"):
        raise HTTPException(status_code=400, detail="view_mode 必须为 original 或 clean")

    # 限制 text_content 长度
    if len(request.text_content) > 5000:
        raise HTTPException(status_code=400, detail="批注内容过长")

    annotation = await note_service.create_annotation(
        db, current_user.id, note_id,
        request.view_mode, request.type, request.text_content,
        request.context_before, request.context_after, request.color,
    )
    return annotation


@router.delete("/{note_id}/annotations/{annotation_id}")
async def delete_annotation_endpoint(
    note_id: str,
    annotation_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """删除批注"""
    success = await note_service.delete_annotation(db, annotation_id, current_user.id, note_id)
    if not success:
        raise HTTPException(status_code=404, detail="批注不存在")
    return {"success": True}


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note_api(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    删除笔记

    删除笔记记录及其在对象存储中的所有关联文件（原始文件、原始 Markdown、清洗后 Markdown）。
    删除操作不可逆。

    Args:
        note_id: 笔记 ID
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        无返回内容（204 No Content）

    Raises:
        HTTPException 404: 笔记不存在或不属于当前用户
    """
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    # 删除笔记记录及关联的存储文件
    await delete_note(db, note)


@router.patch("/{note_id}/role", response_model=NoteResponse)
async def update_note_role(
    note_id: str,
    note_role: str = Query(..., description="笔记角色：material 或 personal_note"),
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    更新笔记角色

    允许在 material（学习资料）和 personal_note（我的笔记）之间切换。

    Args:
        note_id: 笔记 ID
        note_role: 新的笔记角色值
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        NoteResponse: 更新后的笔记信息

    Raises:
        HTTPException 404: 笔记不存在或不属于当前用户
        HTTPException 400: note_role 值无效
    """
    # 验证 note_role 值是否合法
    valid_roles = [e.value for e in NoteRole]
    if note_role not in valid_roles:
        raise HTTPException(
            status_code=400,
            detail=f"无效的 note_role 值: {note_role}，有效值为: {', '.join(valid_roles)}",
        )

    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    note.note_role = NoteRole(note_role)
    await db.commit()
    await db.refresh(note)
    return await _build_note_response(db, note)


def _resolve_storage_path(bucket: str, object_name: str) -> Path:
    """将 bucket/object_name 映射为本地文件系统的绝对路径"""
    return _resolve_path(bucket, object_name)


@router.get("/{note_id}/video")
async def stream_video(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    """
    流式传输视频文件

    支持视频进度条拖拽（HTTP Range 请求）。
    MinIO 模式下重定向到预签名 URL，本地模式下流式返回文件内容。

    Args:
        note_id: 笔记 ID
        current_user: 当前认证用户
        db: 异步数据库会话
        request: 原始 HTTP 请求（用于读取 Range 头）

    Returns:
        StreamingResponse 或 RedirectResponse

    Raises:
        HTTPException 404: 笔记不存在
        HTTPException 400: 笔记不是视频类型
        HTTPException 404: 视频文件不存在
    """
    # 1. 验证笔记归属和类型
    result = await db.execute(
        select(Note).where(Note.id == note_id, Note.user_id == current_user.id)
    )
    note = result.scalars().first()
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")
    if note.source_type != SourceType.video:
        raise HTTPException(status_code=400, detail="该笔记不是视频类型")

    # 2. MinIO 模式：生成预签名 URL 并重定向
    if settings.storage_backend == "minio":
        url = get_presigned_url(settings.minio_bucket_original, note.original_file_path, expires_hours=1)
        return RedirectResponse(url=url)

    # 3. 本地存储：流式返回文件，支持 Range 请求
    video_path = _resolve_storage_path(settings.minio_bucket_original, note.original_file_path)
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="视频文件不存在")

    file_size = video_path.stat().st_size

    # 处理 Range 请求（支持视频进度条拖拽）
    range_header = request.headers.get("range") if request else None

    if range_header:
        # 解析 Range 头（例如 "bytes=0-1023"）
        range_match = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if range_match:
            start = int(range_match.group(1))
            end = int(range_match.group(2)) if range_match.group(2) else file_size - 1
            end = min(end, file_size - 1)
            content_length = end - start + 1

            def iter_file():
                with open(str(video_path), "rb") as f:
                    f.seek(start)
                    remaining = content_length
                    while remaining > 0:
                        chunk_size = min(8192, remaining)
                        data = f.read(chunk_size)
                        if not data:
                            break
                        remaining -= len(data)
                        yield data

            return StreamingResponse(
                iter_file(),
                status_code=206,
                media_type="video/mp4",
                headers={
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(content_length),
                },
            )

    # 无 Range 头：流式返回整个文件
    def iter_file():
        with open(str(video_path), "rb") as f:
            while True:
                data = f.read(8192)
                if not data:
                    break
                yield data

    return StreamingResponse(
        iter_file(),
        media_type="video/mp4",
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
        },
    )


# ============================================================
# 笔记版本历史 API
# ============================================================


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
        raise HTTPException(status_code=404, detail="笔记不存在")

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
        raise HTTPException(status_code=404, detail="笔记不存在")

    # 生成 diff
    try:
        diff_data = await version_service.diff_versions(
            note_id, v1, v2, current_user.id, db
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return NoteVersionDiffResponse(
        v1_number=diff_data["v1_number"],
        v2_number=diff_data["v2_number"],
        diff_lines=diff_data["diff_lines"],
    )


@router.get("/{note_id}/versions/{version_number}")
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
        raise HTTPException(status_code=404, detail="笔记不存在")

    # 读取版本内容
    try:
        content = await version_service.get_version_content(
            note_id, version_number, current_user.id, db
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

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
        raise HTTPException(status_code=404, detail="笔记不存在")

    # 恢复版本
    try:
        new_version = await version_service.restore_version(
            note_id, version_number, current_user.id, db
        )
    except ValueError as e:
        # 区分"版本不存在"和"笔记路径缺失"两种错误
        message = str(e)
        if "不存在" in message and "版本" in message:
            raise HTTPException(status_code=404, detail=message)
        raise HTTPException(status_code=400, detail=message)

    return NoteVersionResponse.model_validate(new_version)
