"""
笔记插图的分发端点（2026-11-09）
================================

## 这个端点解决什么

MinerU 解析带插图的 PDF 后，markdown 里对图写的是**相对引用**：

    ![](images/fc17b4e0…d03.jpg)

图片本身由转换任务搬到 vault 的 `{user_id}/inbox/output/assets/{base}/images/`
（见 `services/note_assets.py`）。浏览器拿到的是 markdown 文本，相对路径没有任何
可解析的基准 —— 所以渲染时前端会把它重写成

    /api/notes/{note_id}/assets/images/fc17b4e0…d03.jpg

本端点就是这个同源 URL 的落地处：校验归属 → 校验路径 → 从 vault 读字节 → 返回。

## 三道判据（都按"宁可 404 也不越界"）

1. **归属**：`note.user_id == current_user.id`，且笔记不在回收站里
   （回收站里的笔记前端不渲染正文，路径也不必可读；同时避免把"已删除但仍
   可访问"变成一条泄漏面）；
2. **路径**：`note_assets.is_safe_asset_path` —— 拒绝 `..`、绝对路径、盘符、
   反斜杠，且后缀必须在图片白名单内（**不含 `.svg`**，它可内嵌脚本）；
3. **存在**：`read_asset` 读不到就 404 —— 中间不区分"对象不存在"与"读取失败"，
   对用户是同一件事，也不泄漏存储细节。

## 为什么用 `:path` 而不是固定一层

MinerU 的图片名是内容哈希，平铺在 `images/` 下；但保留多级路径的能力
（`images/2024/xxx.jpg`）不需要额外成本，而且与对象名结构一一对应。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.auth import get_current_user_dependency
from ...core.app_error import ASSET_NOT_FOUND, ASSET_TYPE_UNSUPPORTED, AppError
from ...database import get_db
from ...models.note import Note
from ...models.user import User
from ...services import note_assets, vault_path

router = APIRouter()


async def _load_owned_note(db: AsyncSession, note_id: str, user_id: str) -> Optional[Note]:
    """取一篇**未进回收站**且属于该用户的笔记；没有则 None"""
    result = await db.execute(
        select(Note).where(
            Note.id == note_id,
            Note.user_id == user_id,
            Note.trashed_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


@router.get(
    "/{note_id}/assets/{asset_path:path}",
    summary="读取笔记内的插图",
    response_class=Response,
)
async def get_note_asset(
    note_id: str,
    asset_path: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """
    返回笔记 markdown 里相对引用指向的插图

    前端渲染 markdown 时把 `images/xxx.jpg` 重写成
    `/api/notes/{note_id}/assets/images/xxx.jpg`（见 `utils/markdown.ts`
    的 `rewriteRelativeImageSources`），本端点负责把字节吐回去。

    Raises:
        AppError: 笔记不存在/不属于当前用户/在回收站（ASSET_NOT_FOUND → 404）
        AppError: 路径不安全或后缀不在白名单（ASSET_TYPE_UNSUPPORTED → 415）
        AppError: 对象不存在（ASSET_NOT_FOUND → 404）
    """
    note = await _load_owned_note(db, note_id, current_user.id)
    if note is None:
        # 与"路径不安全"分开报没有意义：对调用方都是"拿不到这张图"，
        # 但**错误码**要能区分（前端/日志按 code 分流），所以这里仍用
        # 统一的 ASSET_NOT_FOUND，细节见异常消息。
        raise AppError(ASSET_NOT_FOUND, "插图不存在", 404)

    if not note_assets.is_safe_asset_path(asset_path):
        raise AppError(ASSET_TYPE_UNSUPPORTED, "不支持的插图类型或路径", 415)

    prefix = vault_path.derive_prefix(note)
    base = vault_path.derive_base(note)
    data = note_assets.read_asset(prefix, base, asset_path)
    if data is None:
        raise AppError(ASSET_NOT_FOUND, "插图不存在", 404)

    rel = note_assets.normalize_rel_path(asset_path)
    return Response(
        content=data,
        media_type=note_assets.content_type_for(rel) or "application/octet-stream",
        headers={
            # 私有缓存：图与笔记同生命周期，改名/删除后 URL 会变或 404
            "Cache-Control": "private, max-age=86400",
            # 与全站一致：不让浏览器嗅探内容类型（防"图片其实是 HTML"）
            "X-Content-Type-Options": "nosniff",
        },
    )
