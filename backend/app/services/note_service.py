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

from pathlib import PurePosixPath
from typing import Optional, Tuple, List, Dict, Any
import asyncio
import logging
import uuid

from sqlalchemy import select, func, delete as sql_delete, or_, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.note import Note, NoteStatus, NoteRole
from ..models.user import User
from ..models.note_project import NoteProject
from ..models.note_version import NoteVersion
from ..models.folder import Folder
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
    file_exists,
    move_file,
)
from ..config import get_settings
from . import vault_path
from .vault_meta import write_note_meta

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
    project_id: Optional[str] = None,
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
        note_role: 按笔记角色筛选（可选）
        project_id: 按项目筛选（可选）

    Returns:
        Tuple[list[Note], int]: (笔记列表, 总数)
    """
    # 基础查询：只查询当前用户的笔记，排除回收站中的笔记
    query = select(Note).where(Note.user_id == user_id, Note.trashed_at.is_(None))

    # 可选：按状态筛选
    if note_status is not None:
        query = query.where(Note.status == note_status)

    # 可选：按笔记角色筛选（material / personal_note）
    if note_role is not None:
        query = query.where(Note.note_role == NoteRole(note_role))

    # 可选：按项目标签筛选（多对多关联表 EXISTS 查询）
    if project_id is not None:
        query = query.where(
            Note.id.in_(
                select(NoteProject.note_id).where(NoteProject.project_id == project_id)
            )
        )

    # 关键词搜索：使用 ilike 实现不区分大小写的模糊匹配
    # F-32 修复：转义 SQL 通配符（%/_），避免搜索 "100%" 等字面量被放大匹配
    if keyword:
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(Note.title.ilike(f"%{escaped}%", escape="\\"))

    # 先计算总数，用于分页信息
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # 分页查询：按创建时间倒序，最新笔记在前
    query = query.order_by(Note.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    notes = list(result.scalars().all())

    return notes, total


async def get_note_detail(
    db: AsyncSession, note_id: str, user_id: str, include_trashed: bool = False
) -> Optional[Note]:
    """
    获取笔记详情

    同时通过 note_id 和 user_id 查询，确保用户只能访问自己的笔记。
    默认过滤已移入回收站的笔记（与列表一致，软删除后对普通访问表现为
    不存在）；回收站专用端点（restore/purge/trash-info）需传
    include_trashed=True 以定位回收站中的笔记。

    Args:
        db: 数据库会话
        note_id: 笔记 ID
        user_id: 用户 ID，用于权限校验
        include_trashed: 是否包含回收站中的笔记（默认 False）

    Returns:
        Optional[Note]: 找到返回笔记对象，否则返回 None
    """
    conditions = [Note.id == note_id, Note.user_id == user_id]
    if not include_trashed:
        conditions.append(Note.trashed_at.is_(None))
    result = await db.execute(select(Note).where(*conditions))
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


# ==================== 回收站（软删除 / 恢复 / 物理删除） ====================

async def _abort_processing(db: AsyncSession, note: Note, reason: str = "用户手动删除") -> None:
    """处理中状态安全中止：标记 failed 并等待 Celery 任务感知退出"""
    processing_statuses = {
        NoteStatus.converting, NoteStatus.cleaning, NoteStatus.learning,
    }
    if note.status in processing_statuses:
        note.status = NoteStatus.failed
        note.error_message = reason
        await db.commit()
        await db.refresh(note)
        write_note_meta(note)
        logger.info(f"笔记 {note.id[:8]} 处于处理中状态，已标记为 failed")
        # 等待一小段时间，让正在执行的 Celery 任务有机会检测到状态变更
        await asyncio.sleep(0.5)


async def trash_note(db: AsyncSession, note: Note) -> Note:
    """
    移入回收站（软删除）

    原子包语义：数据库子资源（卡片/题目/复习记录/版本/批注/关系/双链）一律不动，
    仅标记 trashed_at 并把物理文件搬到 {user_id}/trash/{note_id}/ 隔离目录
    （vault 路径不含 note_id，不隔离则回收站期间重新上传的同名文件会互相覆盖）。

    文件搬家失败仅 warning 不阻断（回收站详情仍可按 id 查看，恢复时再搬回）。

    Args:
        db: 异步数据库会话
        note: 要移入回收站的笔记对象

    Returns:
        Note: 更新后的笔记对象（trashed_at 已置位、路径已指向 trash 目录）
    """
    note_id = note.id

    # 处理中状态安全中止
    await _abort_processing(db, note, reason="用户移入回收站")

    # ---- 物理文件搬家至回收站隔离目录 ----
    tp = vault_path.trash_prefix(note.user_id, note_id)
    prefix = vault_path.derive_prefix(note)
    base = vault_path.derive_base(note)
    ext = PurePosixPath(note.original_file_path or "").suffix

    moves: List[Tuple[str, str, str]] = []  # (bucket, 旧路径, trash 新路径)
    if note.original_file_path:
        moves.append((settings.minio_bucket_original, note.original_file_path,
                      vault_path.source_object(tp, base, ext)))
    if note.original_md_path:
        moves.append((settings.minio_bucket_markdown, note.original_md_path,
                      vault_path.markdown_object(tp, base)))
    if note.clean_md_path:
        moves.append((settings.minio_bucket_markdown, note.clean_md_path,
                      vault_path.clean_object(tp, base)))

    moved: Dict[str, str] = {}
    for bucket, old, new in moves:
        try:
            if file_exists(bucket, old):
                move_file(bucket, old, new)
                moved[old] = new
        except Exception as e:
            logger.warning(f"回收站文件搬家失败（忽略继续）: {old} -> {new}, 错误: {e}")

    # ---- meta 状态旁载随迁（非 note 字段，按 vault 约定路径计算） ----
    if prefix and base:
        try:
            meta_old = vault_path.meta_object(prefix, base)
            if file_exists(settings.minio_bucket_markdown, meta_old):
                move_file(settings.minio_bucket_markdown, meta_old, vault_path.meta_object(tp, base))
        except Exception as e:
            logger.warning(f"回收站 meta 旁载搬家失败（忽略继续）: note_id={note_id[:8]}, 错误: {e}")

    # ---- 更新路径字段为 trash 路径（仅覆盖实际搬成功的，失败保留原值避免丢信息） ----
    if note.original_file_path and note.original_file_path in moved:
        note.original_file_path = moved[note.original_file_path]
    if note.original_md_path and note.original_md_path in moved:
        note.original_md_path = moved[note.original_md_path]
    if note.clean_md_path and note.clean_md_path in moved:
        note.clean_md_path = moved[note.clean_md_path]

    # ---- 软删除标记（原子包：数据库子资源一律不动） ----
    note.trashed_at = func.now()
    await db.commit()
    await db.refresh(note)
    logger.info(f"笔记已移入回收站: id={note_id[:8]}, 标题={note.title}")
    return note


async def restore_note(db: AsyncSession, note: Note) -> Tuple[Note, Optional[str]]:
    """
    从回收站恢复笔记

    同名冲突处理：恢复时若原位置（inbox）已被同名新文件占用（回收站期间用户
    重新上传了同名文件），自动加序号后缀（base-1、base-2…），四个文件统一
    使用同一新 base（保持 source↔markdown 命名关联法则），笔记标题不动。

    关系/卡片/题目/复习记录在软删除期间原地未动，恢复即整体复原（原子包）。

    Args:
        db: 异步数据库会话
        note: 要恢复的笔记对象（当前路径字段指向 trash 目录）

    Returns:
        Tuple[Note, Optional[str]]: (恢复后的笔记, 改名后的完整文件名或 None)
    """
    note_id = note.id
    prefix = vault_path.inbox_prefix(note.user_id)
    base = vault_path.derive_base(note)  # 从 trash 路径取 stem，仍是原文件主干
    ext = PurePosixPath(note.original_file_path or "").suffix

    def _targets(new_base: str) -> List[Tuple[str, str, str]]:
        """(bucket, trash 源路径, inbox 目标路径) 列表"""
        items = []
        if note.original_file_path:
            items.append((settings.minio_bucket_original, note.original_file_path,
                          vault_path.source_object(prefix, new_base, ext)))
        if note.original_md_path:
            items.append((settings.minio_bucket_markdown, note.original_md_path,
                          vault_path.markdown_object(prefix, new_base)))
        if note.clean_md_path:
            items.append((settings.minio_bucket_markdown, note.clean_md_path,
                          vault_path.clean_object(prefix, new_base)))
        return items

    # ---- 同名冲突检测：目标位置（inbox）被占用则递增后缀，上限 1000 次防御死循环 ----
    new_base = base
    renamed_to: Optional[str] = None
    for i in range(1000):
        candidate = base if i == 0 else f"{base}-{i}"
        if not any(file_exists(b, t) for b, _, t in _targets(candidate)):
            new_base = candidate
            break
    else:
        new_base = f"{base}-999"
    if new_base != base:
        renamed_to = f"{new_base}{ext}"

    # ---- 文件搬回 inbox 目标位 ----
    for bucket, old, new in _targets(new_base):
        try:
            if file_exists(bucket, old):
                move_file(bucket, old, new)
        except Exception as e:
            logger.warning(f"回收站恢复搬家失败（忽略继续）: {old} -> {new}, 错误: {e}")

    # ---- 更新路径字段为 inbox 目标路径 ----
    if note.original_file_path:
        note.original_file_path = vault_path.source_object(prefix, new_base, ext)
    if note.original_md_path:
        note.original_md_path = vault_path.markdown_object(prefix, new_base)
    if note.clean_md_path:
        note.clean_md_path = vault_path.clean_object(prefix, new_base)

    # ---- 恢复可见性 + 防御孤儿文件夹引用 ----
    note.trashed_at = None
    if note.folder_id is not None:
        folder = await db.get(Folder, note.folder_id)
        if folder is None or folder.user_id != note.user_id:
            note.folder_id = None

    await db.commit()
    await db.refresh(note)

    # 按写穿机制刷新 inbox 新位置的 meta 旁载，并清掉 trash 里随迁的旧 meta
    write_note_meta(note)
    tp = vault_path.trash_prefix(note.user_id, note_id)
    try:
        old_meta = vault_path.meta_object(tp, base)
        if file_exists(settings.minio_bucket_markdown, old_meta):
            delete_file(settings.minio_bucket_markdown, old_meta)
    except Exception as e:
        logger.warning(f"清理回收站旧 meta 失败（忽略继续）: note_id={note_id[:8]}, 错误: {e}")

    logger.info(
        f"笔记已从回收站恢复: id={note_id[:8]}, 标题={note.title}, 改名={renamed_to or '无'}"
    )
    return note, renamed_to


async def get_trashed_notes(db: AsyncSession, user_id: str) -> List[Dict[str, Any]]:
    """
    获取回收站笔记列表（含附属统计，作为"恢复可还原什么"的展示依据）

    Returns:
        List[Dict[str, Any]]: 每项含 note（Note 对象）及
        card_count / quiz_count / annotation_count / version_count / link_count
    """
    from ..models.note_annotation import NoteAnnotation
    from ..models.note_material_link import NoteMaterialLink

    result = await db.execute(
        select(Note)
        .where(Note.user_id == user_id, Note.trashed_at.is_not(None))
        .order_by(Note.trashed_at.desc())
    )
    notes = list(result.scalars().all())
    if not notes:
        return []
    note_ids = [n.id for n in notes]

    async def _count_by(model_note_id_col) -> Dict[str, int]:
        rows = (await db.execute(
            select(model_note_id_col, func.count())
            .where(model_note_id_col.in_(note_ids))
            .group_by(model_note_id_col)
        )).all()
        return {row[0]: row[1] for row in rows}

    card_counts = await _count_by(KnowledgeCard.note_id)
    quiz_counts = await _count_by(QuizItem.note_id)
    ann_counts = await _count_by(NoteAnnotation.note_id)
    ver_counts = await _count_by(NoteVersion.note_id)

    # 双向链接数：个人笔记端 + 资料端两路聚合
    link_counts: Dict[str, int] = {nid: 0 for nid in note_ids}
    link_rows = (await db.execute(
        select(NoteMaterialLink.personal_note_id, NoteMaterialLink.material_note_id).where(
            or_(
                NoteMaterialLink.personal_note_id.in_(note_ids),
                NoteMaterialLink.material_note_id.in_(note_ids),
            )
        )
    )).all()
    for pid, mid in link_rows:
        if pid in link_counts:
            link_counts[pid] += 1
        if mid in link_counts:
            link_counts[mid] += 1

    return [
        {
            "note": note,
            "card_count": card_counts.get(note.id, 0),
            "quiz_count": quiz_counts.get(note.id, 0),
            "annotation_count": ann_counts.get(note.id, 0),
            "version_count": ver_counts.get(note.id, 0),
            "link_count": link_counts.get(note.id, 0),
        }
        for note in notes
    ]


async def get_trash_info(db: AsyncSession, note: Note) -> Dict[str, int]:
    """
    获取单条笔记的关联统计（删除确认弹窗文案依据）

    Returns:
        Dict[str, int]: card_count（卡片数）/ key_card_count（核心卡片数）/
        link_count（双向链接数）
    """
    from ..models.note_material_link import NoteMaterialLink
    note_id = note.id

    card_count = (await db.execute(
        select(func.count()).select_from(KnowledgeCard)
        .where(KnowledgeCard.note_id == note_id)
    )).scalar() or 0
    key_card_count = (await db.execute(
        select(func.count()).select_from(KnowledgeCard)
        .where(KnowledgeCard.note_id == note_id, KnowledgeCard.is_key_point.is_(True))
    )).scalar() or 0
    link_count = (await db.execute(
        select(func.count()).select_from(NoteMaterialLink).where(
            or_(
                NoteMaterialLink.personal_note_id == note_id,
                NoteMaterialLink.material_note_id == note_id,
            )
        )
    )).scalar() or 0

    return {
        "card_count": int(card_count),
        "key_card_count": int(key_card_count),
        "link_count": int(link_count),
    }


async def purge_note(db: AsyncSession, note: Note, promote_key_cards: bool = False):
    """
    物理删除笔记（悬挂引用策略）

    与旧版 delete_note 的核心差异——**绝不级联删除关系**：
    - CardRelation：删除剩余卡片行时由外键 ON DELETE SET NULL 自动将被删
      卡片端置 NULL（悬挂），其他卡片处显示"[已删除的笔记]"占位，可手动清理；
    - NoteMaterialLink：行保留，被删笔记端置 NULL 悬挂（不再显式删除链接）。

    删除顺序：
    1. 中止进行中任务
    2. （可选）is_key_point 核心卡片提升为独立节点：note_id 置 NULL，
       其关系/题目（FK SET NULL）/复习历史/掌握度完整保留，复习无缝继续
    3. 删除剩余卡片的 QuizItem + ReviewLog（学习进度随卡片存亡）
    4. 删除剩余卡片行（关系悬挂）
    5. AssessmentResult.is_stale / LearningGoal.scope_notes / DailyPlan 清理
    6. 删存储文件（按 Note 当前记录的路径——trash 后已在 {user}/trash/{note_id}/）、
       Chroma 集合、版本历史
    7. 删 Note 行（note_projects/note_versions/note_annotations 显式双保险删除）

    Args:
        db: 异步数据库会话
        note: 要物理删除的笔记对象
        promote_key_cards: 是否将 is_key_point 核心卡片提升为独立节点
    """
    note_id = note.id

    # ---- 1. 处理中状态安全删除 ----
    await _abort_processing(db, note)

    # ---- 2. 核心卡片提升为独立节点（note_id 可空即独立） ----
    if promote_key_cards:
        promoted = await db.execute(
            sql_update(KnowledgeCard)
            .where(KnowledgeCard.note_id == note_id, KnowledgeCard.is_key_point.is_(True))
            .values(note_id=None)
        )
        if promoted.rowcount:
            logger.info(
                f"核心卡片已提升为独立节点: note={note_id[:8]}, 数量={promoted.rowcount}"
            )

    # ---- 3. 收集剩余（非提升）卡片，删除其题目与复习记录 ----
    card_ids_result = await db.execute(
        select(KnowledgeCard.id).where(KnowledgeCard.note_id == note_id)
    )
    card_ids = [row[0] for row in card_ids_result.all()]

    quiz_ids: List[str] = []
    # 提升卡片的题目（card_id 指向提升卡片）不在删除范围，其 note_id 由 FK SET NULL 悬挂保留
    quiz_ids_result = await db.execute(
        select(QuizItem.id).where(
            or_(
                QuizItem.card_id.in_(card_ids),
                (QuizItem.note_id == note_id) & (QuizItem.card_id.is_(None)),
            )
        )
    )
    quiz_ids = [row[0] for row in quiz_ids_result.all()]

    if quiz_ids:
        await db.execute(
            sql_delete(ReviewLog).where(ReviewLog.quiz_id.in_(quiz_ids))
        )
        await db.execute(
            sql_delete(QuizItem).where(QuizItem.id.in_(quiz_ids))
        )

    # ---- 4. 删除剩余卡片行：CardRelation 由 FK SET NULL 自动悬挂，绝不级联删除 ----
    if card_ids:
        # 先解除待删卡片之间的父子引用（parent_card_id 自引用 FK 为 NO ACTION，
        # 同批多行 DELETE 在 SQLite 逐行外键检查下可能因删除顺序失败）
        await db.execute(
            sql_update(KnowledgeCard)
            .where(KnowledgeCard.parent_card_id.in_(card_ids))
            .values(parent_card_id=None)
        )
        await db.execute(
            sql_delete(KnowledgeCard).where(KnowledgeCard.note_id == note_id)
        )

    # ---- 5. 跨笔记聚合结果软标记与引用清理 ----
    # 标记引用该笔记的 AssessmentResult 为 stale（评估结果保留，提示重新评估）
    from ..models.assessment import AssessmentResult
    ar_result = await db.execute(
        select(AssessmentResult).where(AssessmentResult.user_id == note.user_id)
    )
    for ar in ar_result.scalars().all():
        if note_id in (ar.material_note_ids or []) or note_id in (ar.personal_note_ids or []):
            ar.is_stale = True

    # 清理学习目标 scope_notes 中的失效笔记引用（目标本身保留）
    from ..models.learning_goal import DailyPlan, LearningGoal
    goal_result = await db.execute(
        select(LearningGoal).where(LearningGoal.user_id == note.user_id)
    )
    for goal in goal_result.scalars().all():
        scope = list(goal.scope_notes or [])
        if note_id in scope:
            goal.scope_notes = [n for n in scope if n != note_id]
            logger.info(f"已从学习目标 scope_notes 移除失效笔记: goal_id={goal.id[:8]}, note_id={note_id[:8]}")

    # 清理每日计划推荐任务中的失效引用（note_id / quiz_id / card_id 三路过滤并重算计数）
    plan_result = await db.execute(
        select(DailyPlan).where(DailyPlan.user_id == note.user_id)
    )
    for plan in plan_result.scalars().all():
        tasks = dict(plan.recommended_tasks or {})
        new_tasks = {}
        changed = False
        for key, items in tasks.items():
            if isinstance(items, list):
                kept = [
                    t for t in items
                    if t.get("note_id") != note_id
                    and t.get("quiz_id") not in quiz_ids
                    and t.get("card_id") not in card_ids
                ]
                if len(kept) != len(items):
                    changed = True
                new_tasks[key] = kept
            else:
                new_tasks[key] = items
        if changed:
            plan.recommended_tasks = new_tasks
            plan.total_count = sum(len(v) for v in new_tasks.values() if isinstance(v, list))
            # 已完成数不超过清理后的总数，避免进度溢出
            if plan.completed_count > plan.total_count:
                plan.completed_count = plan.total_count
            logger.info(f"已清理每日计划失效任务: plan_id={plan.id[:8]}, note_id={note_id[:8]}")

    # ---- 清理项目标签关联（显式双保险，即使 CASCADE 已开启） ----
    await db.execute(
        sql_delete(NoteProject).where(NoteProject.note_id == note_id)
    )

    # ---- 清理批注（显式双保险） ----
    from ..models.note_annotation import NoteAnnotation
    ann_result = await db.execute(
        select(NoteAnnotation).where(NoteAnnotation.note_id == note_id)
    )
    for ann in ann_result.scalars().all():
        await db.delete(ann)

    await db.commit()

    # ---- 6. 删除存储文件（按 Note 当前记录路径删：trash 后已在 {user}/trash/{note_id}/） ----
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

    # ---- 删除 Vault 状态旁载 meta/*.json ----
    # 注意：derive_prefix 恒返回 inbox 前缀，trash 后会指错位置（可能误删
    # 回收站期间新上传同名文件的 meta）。这里按当前实际路径推导前缀：
    # {prefix}/source/{base}{ext} → meta 在 {prefix}/output/meta/{base}.json
    if note.original_file_path:
        try:
            parts = note.original_file_path.split("/")
            if len(parts) >= 3:
                prefix = "/".join(parts[:-2])
                base = vault_path.derive_base(note)
                delete_file(settings.minio_bucket_markdown, vault_path.meta_object(prefix, base))
        except Exception as e:
            logger.warning(f"删除状态旁载meta失败: {note_id}, 错误: {e}")

    # ---- 清理 Chroma 向量集合（F-18 修复） ----
    try:
        from ..services.embedding_service import VectorStore
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, VectorStore().delete_note_chunks, note_id)
    except Exception as e:
        logger.warning(f"清理笔记向量数据失败: note_id={note_id}, 错误: {e}")

    # ---- 删除版本历史记录及其存储文件（history/versions/{note_id}/v{N}.md） ----
    try:
        ver_result = await db.execute(
            select(NoteVersion).where(NoteVersion.note_id == note_id)
        )
        versions = ver_result.scalars().all()
        for version in versions:
            if version.storage_path:
                try:
                    delete_file(settings.minio_bucket_markdown, version.storage_path)
                except Exception as e:
                    logger.warning(f"删除版本存储文件失败: {version.storage_path}, 错误: {e}")
            await db.delete(version)
    except Exception as e:
        logger.warning(f"清理版本历史失败: note_id={note_id}, 错误: {e}")

    # ---- 7. 删除笔记记录 ----
    # note_material_links 两端由 FK SET NULL 悬挂保留（其他笔记处显示
    # "[已删除的笔记]"占位，用户可手动清理），绝不级联删除。
    await db.delete(note)
    await db.commit()
    logger.info(f"已物理删除笔记: id={note_id[:8]}, 标题={note.title}, 提升核心卡片={promote_key_cards}")


# 兼容别名：旧名保留（语义 = 不提升核心卡片的物理删除），供既有调用方与测试使用
delete_note = purge_note


async def purge_all_trashed(db: AsyncSession, user_id: str) -> Dict[str, int]:
    """
    清空回收站：遍历当前用户回收站中的所有笔记执行物理删除（不提升核心卡片）

    Returns:
        Dict[str, int]: {"purged": 成功数, "failed": 失败数}
    """
    result = await db.execute(
        select(Note).where(Note.user_id == user_id, Note.trashed_at.is_not(None))
    )
    trashed = list(result.scalars().all())
    purged, failed = 0, 0
    for note in trashed:
        try:
            await purge_note(db, note)
            purged += 1
        except Exception as e:
            failed += 1
            await db.rollback()
            logger.warning(f"清空回收站时单条失败（继续其余）: note_id={note.id[:8]}, 错误: {e}")
    logger.info(f"清空回收站完成: user={user_id[:8]}, 成功={purged}, 失败={failed}")
    return {"purged": purged, "failed": failed}


# ==================== 笔记-资料链接管理 ====================

async def create_note_material_links(db: AsyncSession, user_id: str, personal_note_id: str, material_note_ids: List[str]) -> None:
    """批量创建笔记-资料链接，忽略已存在的；校验 material 归属和角色"""
    from ..models.note_material_link import NoteMaterialLink
    if not material_note_ids:
        return
    # 校验所有 material_note_ids 归属当前用户且角色为 material（回收站笔记不可被关联）
    valid_result = await db.execute(
        select(Note).where(
            Note.id.in_(material_note_ids),
            Note.user_id == user_id,
            Note.note_role == NoteRole.material,
            Note.trashed_at.is_(None),
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
            Note.trashed_at.is_(None),
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
            Note.trashed_at.is_(None),
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

    在覆盖现有 markdown 文件前，先读取当前内容并创建版本快照，
    以支持版本历史查看与历史版本恢复。版本创建失败不影响主保存流程。

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

    # ---- 在覆盖前为当前内容创建版本快照 ----
    # 版本创建失败不应阻塞主保存流程，仅记录警告日志
    try:
        # 读取当前 Markdown 文件内容（覆盖前的快照）
        current_content = ""
        try:
            current_bytes = get_object_bytes(settings.minio_bucket_markdown, path)
            current_content = current_bytes.decode("utf-8")
        except Exception as read_err:
            # 文件可能尚不存在（首次保存），以空内容创建版本快照
            logger.debug(
                f"读取当前 Markdown 失败（可能首次保存）: note_id={note.id[:8]}, err={read_err}"
            )

        # 懒加载 version_service，避免循环导入
        from ..services.version_service import version_service
        from ..models.note_version import VersionSource

        await version_service.create_version(
            note_id=note.id,
            user_id=note.user_id,
            content=current_content,
            source=VersionSource.USER_EDIT.value,
            db=db,
        )
    except Exception as e:
        logger.warning(
            f"创建版本快照失败，继续执行主保存流程: note_id={note.id[:8]}, err={e}"
        )

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
    # 内容变更后同步状态旁载 meta（更新时间/路径变更）
    write_note_meta(note)
    return True
