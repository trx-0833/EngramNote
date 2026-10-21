"""
笔记列表、详情、内容与归档 API（子路由模块）

原 api/notes.py 拆分出的第一块：笔记列表、详情、内容编辑与归档。
所有接口均通过 get_current_user_dependency 确保用户已认证。
笔记是系统的核心资源，用户只能访问自己创建的笔记。
"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.app_error import (
    NOTE_CONTENT_TOO_LARGE,
    NOTE_EXPORT_EMPTY,
    NOTE_MARKDOWN_PATH_MISSING,
    NOTE_NOT_FOUND,
    NOTE_NOT_VIDEO,
    NOTE_ORIGINAL_NOT_EDITABLE,
    NOTE_ROLE_INVALID,
    NOTE_STATUS_INVALID,
    NOTE_VIDEO_FILE_MISSING,
    AppError,
)
from ...database import get_db
from ...models.note import Note, NoteRole, NoteStatus, SourceType
from ...models.user import User
from ...schemas.common import MarkdownDownloadResponse, Mp4StreamResponse
from ...schemas.note import (
    NoteContentUpdateRequest,
    NoteDetailResponse,
    NoteListResponse,
    NoteResponse,
    NoteUpdateRequest,
)
from ...config import get_settings
from ...services import note_service
from ...services.note_export import (
    build_content_disposition,
    build_export_text,
    build_front_matter,
    sanitize_filename,
)
from ...services.note_service import (
    get_clean_markdown_content,
    get_note_detail,
    get_note_markdown_content,
    get_notes_list,
    update_note,
)
from ...services.storage_service import _resolve_path, get_presigned_url
from ...api.auth import get_current_user_dependency

from ._common import (
    _build_note_response,
    _build_note_responses,
    _count_pending_embedding_chunks,
    _fill_project_names,
    _load_project_tags,
)

settings = get_settings()
logger = logging.getLogger(__name__)

router = APIRouter()


def _reject_invalid_note_role(note_role: str, valid_roles: list) -> None:
    """`note_role` 不在白名单里就抛 `NOTE_ROLE_INVALID`

    抽成函数是因为**三个列表端点用的是逐字相同的文案**（列表 / 已归档 / 改角色）。
    写三遍的代价不只是重复：加 `params` 时漏改一处，那一处就会静默回退成中文 ——
    而且只有走那个端点的用户才撞得到。

    `params` 的键与表里的占位符一致（表里是 `{role}` / `{valid}`）。
    """
    raise AppError(
        NOTE_ROLE_INVALID,
        f"无效的 note_role 值: {note_role}，有效值为: {', '.join(valid_roles)}",
        400,
        params={"role": note_role, "valid": ", ".join(valid_roles)},
    )


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
            _reject_invalid_note_role(note_role, valid_roles)
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
            _reject_invalid_note_role(note_role, valid_roles)
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
        AppError: 笔记不存在或不属于当前用户（NOTE_NOT_FOUND → 404）
    """
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise AppError(NOTE_NOT_FOUND, "笔记不存在", 404)

    # 从对象存储中读取 Markdown 内容
    original_md = await get_note_markdown_content(note)
    clean_md = await get_clean_markdown_content(note)

    resp = NoteDetailResponse.model_validate(note)
    resp.original_md_content = original_md
    resp.clean_md_content = clean_md
    # 还没补向量的 chunk 数（B2 的"可见"那半）
    #
    # 为什么在这个端点上算：用户打开笔记详情时**不问问题也能看到**
    # "这篇还没建好索引" —— 而此前只有问答时的 SSE meta 会提示，
    # 也就是说"不提问就不知道"（`docs/overhaul-plan.md` 附录 BN.3 把它列为
    # 「确实影响使用」时缺的正是这一环）。
    #
    # 只对**已经清洗过**的笔记算：还没清洗的笔记根本没有 chunk，
    # 报 0 会让人以为"索引没问题"（而它其实是"还没有内容可索引"）。
    #
    # ⚠️ 这里**还要再包一层 try**：`_count_pending_embedding_chunks` 内部已经
    # 把查询异常转成 `None`，但"转成 None"要靠它自己被成功调用 ——
    # 而它可能在 import、会话获取等更早的环节就抛出来。实测用例
    # `test_query_failure_degrades_to_none_and_does_not_break_the_page`
    # 正是这样把端点打成了 500：**把兜底放在助手函数里，防不住助手函数自己出错**。
    # 这个字段是详情页的附加提示，为了它让整页 500 是本末倒置。
    if note.status in (NoteStatus.cleaned, NoteStatus.archived):
        try:
            resp.pending_embedding_chunks = await _count_pending_embedding_chunks(db, note.id)
        except Exception as exc:  # noqa: BLE001 - 附加提示不该让详情页失败
            logger.warning(
                "笔记详情：待嵌入 chunk 统计失败（该字段留空）| note_id=%s | %s: %s",
                note.id, type(exc).__name__, exc,
            )
            resp.pending_embedding_chunks = None
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
        AppError: 笔记不存在或不属于当前用户（NOTE_NOT_FOUND → 404）
    """
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise AppError(NOTE_NOT_FOUND, "笔记不存在", 404)

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
        raise AppError(NOTE_NOT_FOUND, "笔记不存在", 404)

    # 2. 校验处理中状态
    processing_statuses = {
        NoteStatus.uploading, NoteStatus.converting,
        NoteStatus.cleaning, NoteStatus.learning,
    }
    if note.status in processing_statuses:
        raise AppError(
            NOTE_STATUS_INVALID,
            f"笔记正在处理中（{note.status.value}），暂不可编辑",
            400,
            params={"status": note.status.value},
        )

    # 2.1 原始版内容不可编辑（只读），仅允许编辑清洗版
    if req.target == "original":
        raise AppError(
            NOTE_ORIGINAL_NOT_EDITABLE,
            "原始版内容不可编辑，请切换到清洗版后编辑",
            400,
        )

    # 3. 校验内容大小（5MB 限制，按字节数计算）
    content_size = len(req.content.encode("utf-8"))
    if content_size > 5 * 1024 * 1024:
        raise AppError(
            NOTE_CONTENT_TOO_LARGE,
            "内容过大（超过 5MB），请缩短后重试",
            400,
        )

    # 4. 保存内容
    success = await note_service.save_note_content(db, note, req.content, req.target)
    if not success:
        raise AppError(
            NOTE_MARKDOWN_PATH_MISSING,
            "目标 Markdown 路径为空，资料可能尚未转换完成",
            400,
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
        raise AppError(NOTE_NOT_FOUND, "笔记不存在", 404)

    if note.status not in (
        NoteStatus.archived, NoteStatus.cleaned,
        NoteStatus.learning_failed, NoteStatus.converted,
    ):
        raise AppError(
            NOTE_STATUS_INVALID,
            f"当前状态 {note.status.value} 不允许归档/取消归档操作",
            400,
            params={"status": note.status.value},
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
    from ...services.vault_meta import write_note_meta
    write_note_meta(note)
    return await _build_note_response(db, note)


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
        AppError: 笔记不存在或不属于当前用户（NOTE_NOT_FOUND → 404）
        AppError: note_role 值无效（NOTE_ROLE_INVALID → 400）
    """
    # 验证 note_role 值是否合法
    valid_roles = [e.value for e in NoteRole]
    if note_role not in valid_roles:
        _reject_invalid_note_role(note_role, valid_roles)

    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise AppError(NOTE_NOT_FOUND, "笔记不存在", 404)

    note.note_role = NoteRole(note_role)
    await db.commit()
    await db.refresh(note)
    return await _build_note_response(db, note)


def _resolve_storage_path(bucket: str, object_name: str) -> Path:
    """将 bucket/object_name 映射为本地文件系统的绝对路径"""
    return _resolve_path(bucket, object_name)


@router.get("/{note_id}/video", response_class=Mp4StreamResponse)
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
        AppError: 笔记不存在（NOTE_NOT_FOUND → 404）
        AppError: 笔记不是视频类型（NOTE_NOT_VIDEO → 400）
        AppError: 视频文件不存在（NOTE_VIDEO_FILE_MISSING → 404）
    """
    # 1. 验证笔记归属和类型
    result = await db.execute(
        select(Note).where(Note.id == note_id, Note.user_id == current_user.id)
    )
    note = result.scalars().first()
    if not note:
        raise AppError(NOTE_NOT_FOUND, "笔记不存在", 404)
    if note.source_type != SourceType.video:
        raise AppError(NOTE_NOT_VIDEO, "该笔记不是视频类型", 400)

    # 2. MinIO 模式：生成预签名 URL 并重定向
    if settings.storage_backend == "minio":
        url = get_presigned_url(settings.minio_bucket_original, note.original_file_path, expires_hours=1)
        return RedirectResponse(url=url)

    # 3. 本地存储：流式返回文件，支持 Range 请求
    video_path = _resolve_storage_path(settings.minio_bucket_original, note.original_file_path)
    if not video_path.exists():
        raise AppError(NOTE_VIDEO_FILE_MISSING, "视频文件不存在", 404)

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


@router.get("/{note_id}/export", response_class=MarkdownDownloadResponse)
async def export_note(
    note_id: str,
    variant: str = Query(
        "clean",
        pattern="^(clean|original)$",
        description="导出哪一版正文：clean（清洗版，默认）或 original（原始版）",
    ),
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    导出笔记正文为 Markdown 附件（想法 4「设计导出功能」）

    ## 导出的是"内容"，不是"原文件"

    这一条与 `/{note_id}/video` 的区别要说清：视频端点返回的是**上传的原始字节**
    （所以它支持 Range、MinIO 模式下直接 307 到预签名 URL）；
    本端点返回的是**加工后的正文文本**（front-matter + Markdown），
    内容很小、一次就能给完，因此**不需要 Range，也不走预签名 URL**。

    ## 为什么 `variant` 由调用方指定，而不是"永远导清洗版"

    页面上已有「原始版 / 清洗版」切换。导出跟着**用户当前在看的那一版**走，
    是唯一不需要新增 UI、也不会让用户猜"我导的到底是哪一版"的做法。
    默认 `clean`：清洗版是加工过的、质量更高的那一份，
    也是绝大多数情况下用户想要的。

    ## 为什么不静默回退到另一版

    `variant=clean` 而这篇笔记没有清洗版时，**报错而不是偷偷给原始版**：
    给错版本比报错更糟 —— 用户会拿一份"看起来对、实际是未清洗的 OCR 原文"
    去用，而且无从察觉。前端据 `NOTE_EXPORT_EMPTY` 禁用该选项，
    所以正常路径下撞不到这个错误。

    ## 文件内容

    front-matter（title / source_type / status / variant / page_count /
    created_at / updated_at / exported_at）+ 空行 + **正文逐字原文**。
    正文一个字都不加工 —— 导出是"把应用里的东西拿出来"，
    任何规范化都会让用户怀疑看到的和导出的不是同一份。
    拼接规则见 `services/note_export.py`。

    Args:
        note_id: 笔记 ID
        variant: `clean`（默认）或 `original`
        current_user: 当前认证用户
        db: 异步数据库会话

    Returns:
        StreamingResponse: `text/markdown; charset=utf-8`，
        带 `Content-Disposition: attachment`（文件名由标题安全化而来，
        中文经 RFC 5987 编码，另带 ASCII 回退）

    Raises:
        AppError: 笔记不存在 / 不属于当前用户 / 在回收站中（NOTE_NOT_FOUND → 404）
        AppError: 该版本没有正文（NOTE_EXPORT_EMPTY → 400）
    """
    # 归属与回收站过滤都在 get_note_detail 里（它按 user_id 过滤，
    # 且默认排除 trashed_at 非空的笔记）—— 与同文件其他端点同一口径。
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise AppError(NOTE_NOT_FOUND, "笔记不存在", 404)

    # 两个读取函数在"路径为空"与"读取失败"两种情况下都返回 None，
    # 因此这里统一按"该版本没有正文"处理（见 NOTE_EXPORT_EMPTY 的定义处）。
    if variant == "clean":
        content = await get_clean_markdown_content(note)
        empty_message = "这篇笔记还没有清洗版，请先清洗或改导原始版"
    else:
        content = await get_note_markdown_content(note)
        empty_message = "这篇笔记的原始版内容读不出来"

    # 空串与 None 同等对待：导出空文件比报错更糟 ——
    # 用户会以为这篇笔记本来就是空的。
    if not content:
        raise AppError(NOTE_EXPORT_EMPTY, empty_message, 400)

    front_matter = build_front_matter(
        title=note.title,
        source_type=note.source_type.value,
        status=note.status.value,
        variant=variant,
        page_count=note.page_count,
        created_at=note.created_at,
        updated_at=note.updated_at,
        # 去掉微秒：`created_at` / `updated_at` 是秒级的（数据库里的值），
        # 同一块元信息里两种精度不好看。真浏览器实测发现（`exported_at` 原本是
        # `2026-10-19T04:34:14.595442+00:00`）—— 秒级对"这份文件什么时候导的"
        # 完全够用。
        exported_at=datetime.now(timezone.utc).replace(microsecond=0),
    )
    body = build_export_text(front_matter, content)
    filename_stem = sanitize_filename(note.title, fallback_id=note.id)

    return StreamingResponse(
        iter([body.encode("utf-8")]),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": build_content_disposition(filename_stem),
            # 正文是文本且很小，显式给出长度让浏览器能显示下载进度；
            # 不设 Content-Length 时 Starlette 会走 chunked，行为也对，
            # 但"已知长度"对小文件更友好。
            "Content-Length": str(len(body.encode("utf-8"))),
        },
    )