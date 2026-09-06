"""
笔记本双链与批注 API（子路由模块）

原 api/notes.py 拆分出的双链关系与批注部分。
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_db
from ...models.note import NoteRole
from ...models.user import User
from ...schemas.note_material_link import LinkCreateRequest, LinkListResponse
from ...schemas.note_annotation import (
    AnnotationCreateRequest,
    AnnotationResponse,
    AnnotationListResponse,
)
from ...api.auth import get_current_user_dependency
from ...services import note_service

router = APIRouter()


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
    dangling_material_count = 0

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
        # 悬挂链接数：material_note_id 被物理删除置 NULL 的行数，
        # 供前端显示"[已删除的笔记]"占位和"清理此链接"入口
        from ...models.note_material_link import NoteMaterialLink
        dangling_result = await db.execute(
            select(func.count()).select_from(NoteMaterialLink).where(
                NoteMaterialLink.personal_note_id == note_id,
                NoteMaterialLink.user_id == current_user.id,
                NoteMaterialLink.material_note_id.is_(None),
            )
        )
        dangling_material_count = dangling_result.scalar() or 0
    elif note.note_role == NoteRole.material:
        # 反向查询：获取引用该资料的笔记
        personal_notes = await note_service.get_linked_personal_notes(db, current_user.id, note_id)
        linked_personal_notes = [{"id": n.id, "title": n.title} for n in personal_notes]

    return {
        "personal_note_id": note_id,
        "linked_materials": linked_materials,
        "linked_personal_notes": linked_personal_notes,
        "dangling_material_count": dangling_material_count,
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
        from ...models.assessment import AssessmentResult
        result = await db.execute(
            select(AssessmentResult).where(
                AssessmentResult.user_id == current_user.id,
                AssessmentResult.mode == "quiz",
                AssessmentResult.is_stale.is_(False),
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