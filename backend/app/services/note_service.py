"""
笔记业务逻辑模块

本模块封装笔记相关的核心业务逻辑，被 notes.py API 层调用。
包括笔记的列表查询、详情获取、Markdown 内容读取、更新和删除操作。

主要职责：
- 分页查询笔记列表，支持关键词搜索
- 获取笔记详情（含用户权限校验）
- 从对象存储读取原始和清洗后的 Markdown 内容
- 更新笔记标题
- 删除笔记及其关联的存储文件

设计决策：
- 列表查询先计算总数再分页，确保分页信息准确
- Markdown 内容从对象存储实时读取，不缓存到数据库，保证数据一致性
- 删除笔记时静默忽略存储文件删除失败（文件可能已不存在），确保数据库记录能正常删除
- 关键词搜索使用 ilike 实现模糊匹配，不区分大小写
"""

from __future__ import annotations

from typing import Optional, Tuple, List
import asyncio
import logging
import uuid

from sqlalchemy import select, func, delete as sql_delete, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.note import Note, NoteStatus, NoteRole
from ..models.user import User
from ..models.knowledge_card import KnowledgeCard
from ..models.quiz_item import QuizItem
from ..models.review_log import ReviewLog
from ..models.card_relation import CardRelation
from ..schemas.note import NoteUpdateRequest
from ..services.storage_service import (
    delete_file,
    get_object_bytes,
    get_presigned_url,
    upload_bytes,
)
from ..config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


async def get_notes_list(
    db: AsyncSession,
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    keyword: Optional[str] = None,
    note_status: Optional[NoteStatus] = None,
    note_role: Optional[str] = None,
) -> Tuple[list[Note], int]:
    """
    获取用户的笔记列表（分页）

    查询流程：
    1. 构建基础查询条件（按用户 ID 过滤）
    2. 可选：按标题关键词模糊搜索
    3. 可选：按状态筛选
    4. 计算符合条件的总数
    5. 按创建时间倒序分页查询

    Args:
        db: 异步数据库会话
        user_id: 用户 ID，确保只查询该用户的笔记
        page: 页码，从 1 开始
        page_size: 每页数量
        keyword: 搜索关键词，按标题模糊匹配（可选）
        note_status: 按状态筛选（可选）

    Returns:
        Tuple[list[Note], int]: (笔记列表, 总数)
    """
    # 基础查询：只查询当前用户的笔记
    query = select(Note).where(Note.user_id == user_id)

    # 可选：按状态筛选
    if note_status is not None:
        query = query.where(Note.status == note_status)

    # 可选：按笔记角色筛选（material / personal_note）
    if note_role is not None:
        query = query.where(Note.note_role == NoteRole(note_role))

    # 关键词搜索：使用 ilike 实现不区分大小写的模糊匹配
    if keyword:
        query = query.where(Note.title.ilike(f"%{keyword}%"))

    # 先计算总数，用于分页信息
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # 分页查询：按创建时间倒序，最新笔记在前
    query = query.order_by(Note.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    notes = list(result.scalars().all())

    return notes, total


async def get_note_detail(db: AsyncSession, note_id: str, user_id: str) -> Optional[Note]:
    """
    获取笔记详情

    同时通过 note_id 和 user_id 查询，确保用户只能访问自己的笔记。

    Args:
        db: 异步数据库会话
        note_id: 笔记 ID
        user_id: 用户 ID，用于权限校验

    Returns:
        Optional[Note]: 找到返回笔记对象，否则返回 None
    """
    result = await db.execute(
        select(Note).where(Note.id == note_id, Note.user_id == user_id)
    )
    return result.scalars().first()


async def get_note_markdown_content(note: Note) -> Optional[str]:
    """
    从对象存储读取笔记的原始 Markdown 内容

    即 Mineru/ASR 转换后未经清洗的 Markdown 文本。

    Args:
        note: 笔记对象

    Returns:
        Optional[str]: Markdown 文本内容，读取失败或路径为空返回 None
    """
    if not note.original_md_path:
        return None
    try:
        data = get_object_bytes(settings.minio_bucket_markdown, note.original_md_path)
        return data.decode("utf-8")
    except Exception:
        # 对象存储读取失败时返回 None，不影响其他数据返回
        return None


async def get_clean_markdown_content(note: Note) -> Optional[str]:
    """
    从对象存储读取清洗后的 Markdown 内容

    即经过 AI 清洗优化后的 Markdown 文本，质量更高。

    Args:
        note: 笔记对象

    Returns:
        Optional[str]: 清洗后的 Markdown 文本内容，读取失败或路径为空返回 None
    """
    if not note.clean_md_path:
        return None
    try:
        data = get_object_bytes(settings.minio_bucket_markdown, note.clean_md_path)
        return data.decode("utf-8")
    except Exception:
        return None


async def update_note(db: AsyncSession, note: Note, req: NoteUpdateRequest) -> Note:
    """
    更新笔记

    目前仅支持修改笔记标题。仅更新请求中明确提供的字段（非 None）。

    Args:
        db: 异步数据库会话
        note: 要更新的笔记对象
        req: 更新请求体，包含需要修改的字段

    Returns:
        Note: 更新后的笔记对象
    """
    if req.title is not None:
        note.title = req.title
    await db.commit()
    await db.refresh(note)
    return note


async def delete_note(db: AsyncSession, note: Note):
    """
    删除笔记及其所有关联数据

    删除顺序（按外键依赖，从叶子到根）：
    1. ReviewLog（依赖 QuizItem）
    2. CardRelation（依赖 KnowledgeCard）
    3. QuizItem（依赖 KnowledgeCard + Note）
    4. KnowledgeCard（依赖 Note）
    5. MinIO 存储文件
    6. Note 记录

    对于处理中状态（converting/cleaning/learning）的笔记：
    先将状态标记为 failed，阻止后续 Celery 任务继续处理，
    等待 0.5 秒让正在运行的任务检测到状态变更后提前退出，再执行级联删除。

    Args:
        db: 异步数据库会话
        note: 要删除的笔记对象
    """
    note_id = note.id

    # ---- 处理中状态安全删除 ----
    processing_statuses = {
        NoteStatus.converting, NoteStatus.cleaning, NoteStatus.learning,
    }
    if note.status in processing_statuses:
        note.status = NoteStatus.failed
        note.error_message = "用户手动删除"
        await db.commit()
        logger.info(f"笔记 {note_id[:8]} 处于处理中状态，已标记为 failed")
        # 等待一小段时间，让正在执行的 Celery 任务有机会检测到状态变更
        await asyncio.sleep(0.5)

    # ---- 级联删除（按外键依赖顺序） ----

    # 1. 查找该笔记下所有知识卡片 ID
    card_ids_result = await db.execute(
        select(KnowledgeCard.id).where(KnowledgeCard.note_id == note_id)
    )
    card_ids = [row[0] for row in card_ids_result.all()]

    if card_ids:
        # 2. 删除关联的卡片关系
        await db.execute(
            sql_delete(CardRelation).where(
                or_(
                    CardRelation.card_id_1.in_(card_ids),
                    CardRelation.card_id_2.in_(card_ids),
                )
            )
        )

        # 3. 查找关联的题目 ID
        quiz_ids_result = await db.execute(
            select(QuizItem.id).where(QuizItem.note_id == note_id)
        )
        quiz_ids = [row[0] for row in quiz_ids_result.all()]

        if quiz_ids:
            # 4. 删除关联的复习记录
            await db.execute(
                sql_delete(ReviewLog).where(ReviewLog.quiz_id.in_(quiz_ids))
            )

        # 5. 删除关联的题目
        await db.execute(
            sql_delete(QuizItem).where(QuizItem.note_id == note_id)
        )

        # 6. 删除关联的知识卡片
        await db.execute(
            sql_delete(KnowledgeCard).where(KnowledgeCard.note_id == note_id)
        )

    # ---- 清理笔记-资料链接 ----
    # 删除涉及该笔记的所有链接（正向和反向）
    await delete_note_material_links(db, note_id)

    # ---- 清理批注 ----
    from ..models.note_annotation import NoteAnnotation
    ann_result = await db.execute(
        select(NoteAnnotation).where(NoteAnnotation.note_id == note_id)
    )
    for ann in ann_result.scalars().all():
        await db.delete(ann)

    # ---- 标记引用该笔记的 AssessmentResult 为 stale ----
    from ..models.assessment import AssessmentResult
    ar_result = await db.execute(
        select(AssessmentResult).where(AssessmentResult.user_id == note.user_id)
    )
    for ar in ar_result.scalars().all():
        if note_id in (ar.material_note_ids or []) or note_id in (ar.personal_note_ids or []):
            ar.is_stale = True
    await db.commit()

    # ---- 删除 MinIO 文件 ----
    if note.original_file_path:
        try:
            delete_file(settings.minio_bucket_original, note.original_file_path)
        except Exception as e:
            logger.warning(f"删除原始文件失败: {note.original_file_path}, 错误: {e}")

    if note.original_md_path:
        try:
            delete_file(settings.minio_bucket_markdown, note.original_md_path)
        except Exception as e:
            logger.warning(f"删除原始Markdown失败: {note.original_md_path}, 错误: {e}")

    if note.clean_md_path:
        try:
            delete_file(settings.minio_bucket_markdown, note.clean_md_path)
        except Exception as e:
            logger.warning(f"删除清洗Markdown失败: {note.clean_md_path}, 错误: {e}")

    # ---- 删除笔记记录 ----
    await db.delete(note)
    await db.commit()
    logger.info(f"已删除笔记: id={note_id[:8]}, 标题={note.title}")


# ==================== 笔记-资料链接管理 ====================

async def create_note_material_links(db: AsyncSession, user_id: str, personal_note_id: str, material_note_ids: List[str]) -> None:
    """批量创建笔记-资料链接，忽略已存在的；校验 material 归属和角色"""
    from ..models.note_material_link import NoteMaterialLink
    if not material_note_ids:
        return
    # 校验所有 material_note_ids 归属当前用户且角色为 material
    valid_result = await db.execute(
        select(Note).where(
            Note.id.in_(material_note_ids),
            Note.user_id == user_id,
            Note.note_role == NoteRole.material,
        )
    )
    valid_ids = {n.id for n in valid_result.scalars().all()}
    for material_id in material_note_ids:
        if material_id not in valid_ids:
            logger.warning(f"跳过非法 material_id: {material_id}（不属于用户或非 material 角色）")
            continue
        # 检查是否已存在
        existing = await db.execute(
            select(NoteMaterialLink).where(
                NoteMaterialLink.personal_note_id == personal_note_id,
                NoteMaterialLink.material_note_id == material_id,
            )
        )
        if not existing.scalars().first():
            link = NoteMaterialLink(
                id=str(uuid.uuid4()),
                user_id=user_id,
                personal_note_id=personal_note_id,
                material_note_id=material_id,
            )
            db.add(link)
    await db.commit()


async def get_linked_materials(db: AsyncSession, user_id: str, personal_note_id: str) -> List[Note]:
    """获取笔记关联的资料列表"""
    from ..models.note_material_link import NoteMaterialLink
    result = await db.execute(
        select(Note).join(
            NoteMaterialLink, NoteMaterialLink.material_note_id == Note.id
        ).where(
            NoteMaterialLink.personal_note_id == personal_note_id,
            NoteMaterialLink.user_id == user_id,
        )
    )
    return result.scalars().all()


async def get_linked_personal_notes(db: AsyncSession, user_id: str, material_note_id: str) -> List[Note]:
    """获取引用该资料的笔记列表（反向查询）"""
    from ..models.note_material_link import NoteMaterialLink
    result = await db.execute(
        select(Note).join(
            NoteMaterialLink, NoteMaterialLink.personal_note_id == Note.id
        ).where(
            NoteMaterialLink.material_note_id == material_note_id,
            NoteMaterialLink.user_id == user_id,
        )
    )
    return result.scalars().all()


async def update_note_material_links(db: AsyncSession, user_id: str, personal_note_id: str, material_note_ids: List[str]) -> bool:
    """更新笔记-资料链接，返回是否发生变化"""
    from ..models.note_material_link import NoteMaterialLink
    # 获取当前链接
    result = await db.execute(
        select(NoteMaterialLink).where(NoteMaterialLink.personal_note_id == personal_note_id)
    )
    current_links = result.scalars().all()
    current_ids = {link.material_note_id for link in current_links}
    new_ids = set(material_note_ids)

    if current_ids == new_ids:
        return False  # 无变化

    # 删除不再需要的链接
    for link in current_links:
        if link.material_note_id not in new_ids:
            await db.delete(link)

    # 新增新链接
    for material_id in new_ids - current_ids:
        link = NoteMaterialLink(
            id=str(uuid.uuid4()),
            user_id=user_id,
            personal_note_id=personal_note_id,
            material_note_id=material_id,
        )
        db.add(link)

    await db.commit()
    return True


async def delete_note_material_links(db: AsyncSession, note_id: str) -> None:
    """删除涉及该笔记的所有链接（正向和反向）"""
    from ..models.note_material_link import NoteMaterialLink
    result = await db.execute(
        select(NoteMaterialLink).where(
            (NoteMaterialLink.personal_note_id == note_id) |
            (NoteMaterialLink.material_note_id == note_id)
        )
    )
    links = result.scalars().all()
    for link in links:
        await db.delete(link)
    await db.commit()


# ==================== 笔记批注管理 ====================

async def create_annotation(db: AsyncSession, user_id: str, note_id: str, view_mode: str, type: str, text_content: str, context_before: str = "", context_after: str = "", color: Optional[str] = None) -> NoteAnnotation:
    """创建批注"""
    from ..models.note_annotation import NoteAnnotation
    annotation = NoteAnnotation(
        id=str(uuid.uuid4()),
        user_id=user_id,
        note_id=note_id,
        view_mode=view_mode,
        type=type,
        text_content=text_content,
        context_before=context_before,
        context_after=context_after,
        color=color,
    )
    db.add(annotation)
    await db.commit()
    await db.refresh(annotation)
    return annotation


async def get_annotations(db: AsyncSession, note_id: str, user_id: str, view_mode: Optional[str] = None) -> List[NoteAnnotation]:
    """获取笔记的批注列表"""
    from ..models.note_annotation import NoteAnnotation
    query = select(NoteAnnotation).where(
        NoteAnnotation.note_id == note_id,
        NoteAnnotation.user_id == user_id,
    )
    if view_mode:
        query = query.where(NoteAnnotation.view_mode == view_mode)
    query = query.order_by(NoteAnnotation.created_at.desc())
    result = await db.execute(query)
    return result.scalars().all()


async def delete_annotation(db: AsyncSession, annotation_id: str, user_id: str, note_id: str) -> bool:
    """删除批注，校验 note_id 归属"""
    from ..models.note_annotation import NoteAnnotation
    result = await db.execute(
        select(NoteAnnotation).where(
            NoteAnnotation.id == annotation_id,
            NoteAnnotation.user_id == user_id,
            NoteAnnotation.note_id == note_id,
        )
    )
    annotation = result.scalars().first()
    if annotation:
        await db.delete(annotation)
        await db.commit()
        return True
    return False


async def save_note_content(db: AsyncSession, note: Note, content: str, target: str = "clean") -> bool:
    """
    保存用户编辑的 Markdown 内容到对象存储

    Args:
        db: 异步数据库会话
        note: 笔记对象
        content: Markdown 文本内容
        target: 写入目标，"clean" 写入 clean_md_path，"original" 写入 original_md_path

    Returns:
        bool: 写入成功返回 True，路径为空返回 False

    注意：不新增数据库字段，直接覆盖对象存储中的 markdown 文件
    """
    # 根据目标选择写入路径
    if target == "clean":
        path = note.clean_md_path
    else:
        path = note.original_md_path

    if not path:
        return False  # 路径为空，资料未转换完成

    # 将内容转为字节流
    content_bytes = content.encode("utf-8")

    # 调用 storage_service 写入对象存储
    upload_bytes(
        settings.minio_bucket_markdown,
        path,
        content_bytes,
        content_type="text/markdown",
    )

    logger.info(f"已保存笔记内容: note_id={note.id[:8]}, target={target}, size={len(content_bytes)} bytes")

    # 刷新 updated_at（onupdate=func.now() 仅在 ORM 字段更新时触发，对象存储写入不触发）
    note.updated_at = func.now()
    await db.commit()
    await db.refresh(note)
    return True
