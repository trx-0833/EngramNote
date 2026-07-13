"""
知识点管理 API 模块

本模块提供知识卡片联合分析、拓展生成、标记、盲点查询与掌握度概览等
HTTP 接口，对应 Q4~Q9 中"联合分析→盲点→拓展→出题→标记→掌握度"链路。

主要职责：
- 手动触发笔记-资料联合分析（POST /links/{link_id}/extract-combined）
- 基于已掌握父卡片生成拓展知识点（POST /cards/{card_id}/generate-extension）
- 为拓展卡片立即触发出题任务（POST /cards/{card_id}/generate-questions）
- 标记卡片为重点/难点（PATCH /cards/{card_id}/mark）
- 查询盲点卡片列表（GET /blind-spots）
- 查询掌握度概览（GET /mastery）

设计决策：
- 所有接口均需认证（Depends(get_current_user_dependency)），并通过 user_id 校验资源归属防 IDOR
- 联合分析 / 拓展生成调用 Service 层，错误以 dict 含 "error" 键返回，由本层转 HTTPException
- 盲点列表因 source_note_ids 为 JSON 列，采用 Python 端过滤 + 切片分页
- 掌握度概览用一次性 group_by 查询每张卡片的 quiz 数量，避免 N+1
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.user import User
from ..models.knowledge_card import KnowledgeCard, CardCategory
from ..models.note_material_link import NoteMaterialLink
from ..models.quiz_item import QuizItem
from ..api.auth import get_current_user_dependency
from ..schemas.knowledge import (
    KnowledgeCardResponse,
    CardMarkRequest,
    ExtensionGenerateRequest,
    CombinedExtractResponse,
    ExtensionGenerateResponse,
    BlindSpotListResponse,
    MasteryOverviewItem,
    MasteryOverviewResponse,
)
from ..services.knowledge_link_service import extract_combined, generate_extension
from ..tasks.understand_tasks import generate_questions_task

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/links/{link_id}/extract-combined", response_model=CombinedExtractResponse)
async def extract_combined_endpoint(
    link_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    手动触发笔记-资料联合分析

    调用 extract_combined 服务：内部会校验 link 归属（user_id），
    命中缓存则返回已有统计，否则重新提取常规知识点与盲点。

    Raises:
        HTTPException 400: 联合分析失败（如关联不存在、内容为空等）
    """
    result = await extract_combined(link_id, current_user.id, db)
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return CombinedExtractResponse(**result)


@router.post("/cards/{card_id}/generate-extension", response_model=ExtensionGenerateResponse)
async def generate_extension_endpoint(
    card_id: str,
    req: ExtensionGenerateRequest,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    基于已掌握的父卡片生成进阶拓展知识点

    调用 generate_extension 服务：校验父卡片归属与掌握度阈值（>=80），
    调用 LLM 生成拓展卡片并持久化。

    Raises:
        HTTPException 400: 生成失败（父卡片不存在、掌握度不足、LLM 调用失败等）
    """
    result = await generate_extension(
        card_id, current_user.id, db, material_note_id=req.material_note_id
    )
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return ExtensionGenerateResponse(**result)


@router.post("/cards/{card_id}/generate-questions")
async def generate_questions_for_extension(
    card_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    为拓展卡片立即触发出题任务（Q9 提示后的确认动作）

    仅允许 card_category == extension 的卡片调用此端点。
    触发 Celery generate_questions_task，定向生成拓展类难题。

    Raises:
        HTTPException 404: 卡片不存在或无权访问
        HTTPException 400: 非拓展卡片不允许使用此端点
    """
    # 查询卡片并校验归属
    result = await db.execute(
        select(KnowledgeCard).where(
            KnowledgeCard.id == card_id,
            KnowledgeCard.user_id == current_user.id,
        )
    )
    card = result.scalars().first()
    if not card:
        raise HTTPException(status_code=404, detail="知识卡片不存在或无权访问")

    # 仅允许拓展卡片用此端点立即出题
    if card.card_category != CardCategory.extension:
        raise HTTPException(status_code=400, detail="仅拓展卡片可使用此端点")

    # 触发 Celery 出题任务（定向：拓展类别 + 困难难度）
    generate_questions_task.delay(
        card.note_id,
        target_categories=["extension"],
        target_difficulty="hard",
    )

    return {
        "card_id": card_id,
        "note_id": card.note_id,
        "message": "拓展卡片出题任务已触发",
        "target_categories": ["extension"],
    }


@router.patch("/cards/{card_id}/mark", response_model=KnowledgeCardResponse)
async def mark_card(
    card_id: str,
    req: CardMarkRequest,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    标记卡片为重点 / 难点

    仅更新请求中提供的字段（非 None 才更新）。

    Raises:
        HTTPException 404: 卡片不存在或无权访问
    """
    result = await db.execute(
        select(KnowledgeCard).where(
            KnowledgeCard.id == card_id,
            KnowledgeCard.user_id == current_user.id,
        )
    )
    card = result.scalars().first()
    if not card:
        raise HTTPException(status_code=404, detail="知识卡片不存在或无权访问")

    if req.is_key_point is not None:
        card.is_key_point = req.is_key_point
    if req.is_difficulty is not None:
        card.is_difficulty = req.is_difficulty

    await db.commit()
    await db.refresh(card)

    return KnowledgeCardResponse.model_validate(card)


@router.get("/blind-spots", response_model=BlindSpotListResponse)
async def list_blind_spots(
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
    link_id: Optional[str] = Query(None, description="按笔记-资料关联过滤"),
    material_id: Optional[str] = Query(None, description="按资料 ID 过滤 source_note_ids"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """
    查询当前用户的盲点卡片列表

    支持按 link_id（笔记-资料关联）或 material_id（资料 ID）过滤。
    由于 source_note_ids 为 JSON 列，采用先查全部盲点卡片再 Python 端过滤后切片分页的方式。

    - link_id：查询 NoteMaterialLink（校验归属）→ 用 personal_note_id 与 material_note_id
      过滤 source_note_ids 同时含两 ID 的卡片
    - material_id：过滤 source_note_ids 包含该 ID 的卡片
    """
    # 基础条件：当前用户的盲点卡片
    result = await db.execute(
        select(KnowledgeCard).where(
            KnowledgeCard.user_id == current_user.id,
            KnowledgeCard.card_category == CardCategory.blind_spot,
        )
    )
    cards = list(result.scalars().all())

    # link_id 过滤：查 NoteMaterialLink 拿 personal_note_id 与 material_note_id
    link_personal_id: Optional[str] = None
    link_material_id: Optional[str] = None
    if link_id:
        link_result = await db.execute(
            select(NoteMaterialLink).where(
                NoteMaterialLink.id == link_id,
                NoteMaterialLink.user_id == current_user.id,
            )
        )
        link = link_result.scalars().first()
        if not link:
            raise HTTPException(status_code=404, detail="笔记-资料关联不存在或无权访问")
        link_personal_id = link.personal_note_id
        link_material_id = link.material_note_id

    # Python 端过滤 source_note_ids（JSON 列）
    filtered: List[KnowledgeCard] = []
    for card in cards:
        ids = card.source_note_ids or []
        if link_id:
            # 同时包含 personal_note_id 与 material_note_id
            if link_personal_id in ids and link_material_id in ids:
                filtered.append(card)
        elif material_id:
            if material_id in ids:
                filtered.append(card)
        else:
            filtered.append(card)

    total = len(filtered)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = filtered[start:end]

    return BlindSpotListResponse(
        items=[KnowledgeCardResponse.model_validate(card) for card in page_items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/mastery", response_model=MasteryOverviewResponse)
async def mastery_overview(
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    card_category: Optional[str] = Query(None, description="按类别过滤：regular/blind_spot/extension"),
):
    """
    查询当前用户的知识卡片掌握度概览

    按 mastery_level ASC 排序（掌握度低的在前，便于关注薄弱点）。
    total 与 average_mastery 基于全部匹配卡片计算（非仅当前页）。
    review_count 通过一次性 group_by 查询每张卡片下的 QuizItem 数量。
    """
    # 构建查询条件
    conditions = [KnowledgeCard.user_id == current_user.id]
    if card_category:
        try:
            cat = CardCategory(card_category)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"无效的卡片类别: {card_category}，合法值为 regular/blind_spot/extension",
            )
        conditions.append(KnowledgeCard.card_category == cat)

    # 计算总数与平均掌握度（基于全部匹配卡片）
    stats_query = select(
        func.count().label("total"),
        func.avg(KnowledgeCard.mastery_level).label("avg_mastery"),
    ).where(*conditions)
    stats_row = (await db.execute(stats_query)).one()
    total = stats_row[0] or 0
    average_mastery = float(stats_row[1]) if stats_row[1] is not None else 0.0

    # 分页查询（掌握度低的在前）
    page_query = (
        select(KnowledgeCard)
        .where(*conditions)
        .order_by(KnowledgeCard.mastery_level.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    page_result = await db.execute(page_query)
    page_cards = list(page_result.scalars().all())

    # 一次性 group_by 查询当前页卡片的 QuizItem 数量，避免 N+1
    quiz_counts: Dict[str, int] = {}
    if page_cards:
        card_ids = [card.id for card in page_cards]
        count_result = await db.execute(
            select(QuizItem.card_id, func.count(QuizItem.id).label("cnt"))
            .where(QuizItem.card_id.in_(card_ids))
            .group_by(QuizItem.card_id)
        )
        quiz_counts = {row[0]: row[1] for row in count_result.all()}

    items = [
        MasteryOverviewItem(
            card_id=card.id,
            title=card.title,
            card_category=card.card_category,
            mastery_level=card.mastery_level,
            is_key_point=card.is_key_point,
            is_difficulty=card.is_difficulty,
            review_count=quiz_counts.get(card.id, 0),
        )
        for card in page_cards
    ]

    return MasteryOverviewResponse(
        items=items,
        total=total,
        average_mastery=average_mastery,
    )
