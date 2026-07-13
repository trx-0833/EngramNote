"""
联合分析服务模块

提供笔记-资料联合分析（盲点检测）与拓展知识点生成功能。

主要职责：
- 计算笔记-资料配对签名（link_signature），用于缓存联合分析结果
- 多轮逐章节比对学习资料与用户笔记，提取常规知识点（regular）与盲点（blind_spot）
- 基于已掌握（mastery_level >= 80）的父卡片生成进阶拓展知识点（extension）

设计决策：
- 联合分析结果通过 AssessmentResult（mode=combined_extract）缓存，
  复用 assessment_service 的 link_signature 缓存模式（基于配对 ID 做 SHA-256）
- 多轮对话使用 CombinedAnalysisSession（轻量级标题列表去重，节省 token）
- 仅删除本次 link 产生的卡片（按 source_note_ids 同时包含 personal_id 与 material_id 过滤），
  避免误删用户从单笔记理解管道得到的 regular 卡片
- 解析 LLM JSON 响应时容错：单轮解析失败仅记 warning 并跳过，不阻塞整体流程
- 校验/失败场景返回 {"error": ...} 字典，由 API 层转 HTTPException，不抛异常
"""

import hashlib
import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.assessment import AssessmentResult, AssessmentMode
from ..models.knowledge_card import CardCategory, CardType, KnowledgeCard
from ..models.note_material_link import NoteMaterialLink
from ..schemas.knowledge import KnowledgeCardResponse
from ..services.llm_service import LLMService
from ..services.note_service import (
    get_clean_markdown_content,
    get_note_detail,
    get_note_markdown_content,
)
from ..services.understanding_service import split_into_chapters

logger = logging.getLogger(__name__)

# 拓展生成的掌握度阈值（Q4）
EXTENSION_MASTERY_THRESHOLD = 80.0
# source_text 截断长度，与 understanding_service 保持一致
MAX_SOURCE_TEXT_LEN = 5000
# 单章节内容截断长度
MAX_CHAPTER_CONTENT_LEN = 8000
# 拓展生成资料上下文长度
EXTENSION_MATERIAL_CONTEXT_LEN = 2000


def compute_link_signature(material_id: str, personal_id: str) -> str:
    """
    计算笔记-资料配对签名

    基于 material_id + personal_id 做 SHA-256 哈希，返回 16 位十六进制。
    与 AssessmentService._compute_link_signature 保持一致的模式（排序后拼接再哈希），
    用于 AssessmentResult 缓存键。内容变化导致的缓存失效通过 is_stale 标志维护
    （与现有 assessment_service 一致）。

    Args:
        material_id: 学习资料笔记 ID
        personal_id: 用户笔记 ID

    Returns:
        str: 16 位十六进制签名
    """
    sorted_ids = sorted([material_id, personal_id])
    combined = ",".join(sorted_ids)
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


def _parse_card_type(card_type_str: str) -> CardType:
    """容错解析 card_type，非法值降级为 concept"""
    try:
        return CardType(card_type_str)
    except (ValueError, KeyError):
        return CardType.concept


def _truncate_source_text(source_text: str) -> str:
    """截断 source_text，避免过长"""
    if not source_text:
        return ""
    if len(source_text) > MAX_SOURCE_TEXT_LEN:
        return source_text[:MAX_SOURCE_TEXT_LEN]
    return source_text


def _build_combined_card(
    user_id: str,
    personal_note_id: str,
    material_id: str,
    point: Dict[str, Any],
    chapter_title: str,
    category: CardCategory,
) -> KnowledgeCard:
    """
    根据联合分析的单个知识点构建 KnowledgeCard（未持久化）

    Args:
        user_id: 用户 ID
        personal_note_id: 用户笔记 ID（卡片的 note_id）
        material_id: 学习资料 ID
        point: LLM 返回的单个知识点字典
        chapter_title: 章节标题
        category: 卡片分类（regular / blind_spot）

    Returns:
        KnowledgeCard: 未加入 session 的瞬态卡片对象
    """
    card_type = _parse_card_type(point.get("card_type", "concept"))
    return KnowledgeCard(
        user_id=user_id,
        note_id=personal_note_id,
        card_type=card_type,
        title=point.get("title", "未命名知识点") or "未命名知识点",
        content=point.get("content", "") or "",
        chapter_title=chapter_title,
        source_text=_truncate_source_text(point.get("source_text", "") or ""),
        card_category=category,
        is_key_point=bool(point.get("is_key_point", False)),
        is_difficulty=bool(point.get("is_difficulty", False)),
        source_note_ids=[material_id, personal_note_id],
    )


async def _select_combined_cards(
    db: AsyncSession, user_id: str, personal_id: str, material_id: str
) -> List[KnowledgeCard]:
    """
    查询本次 link 联合分析产生的卡片

    过滤条件：note_id=personal_id、card_category in (regular, blind_spot)、
    source_note_ids 同时包含 personal_id 与 material_id。
    """
    result = await db.execute(
        select(KnowledgeCard).where(
            KnowledgeCard.user_id == user_id,
            KnowledgeCard.note_id == personal_id,
            KnowledgeCard.card_category.in_(
                [CardCategory.regular, CardCategory.blind_spot]
            ),
        )
    )
    candidates = result.scalars().all()
    matched: List[KnowledgeCard] = []
    for card in candidates:
        ids = card.source_note_ids or []
        if personal_id in ids and material_id in ids:
            matched.append(card)
    return matched


async def _count_combined_cards(
    db: AsyncSession, user_id: str, personal_id: str, material_id: str
) -> Tuple[int, int]:
    """统计本次 link 联合分析的 regular / blind_spot 卡片数量"""
    cards = await _select_combined_cards(db, user_id, personal_id, material_id)
    regular = sum(1 for c in cards if c.card_category == CardCategory.regular)
    blind = sum(1 for c in cards if c.card_category == CardCategory.blind_spot)
    return regular, blind


async def _delete_combined_cards(
    db: AsyncSession, user_id: str, personal_id: str, material_id: str
) -> None:
    """删除本次 link 联合分析产生的旧卡片（仅 regular / blind_spot）"""
    cards = await _select_combined_cards(db, user_id, personal_id, material_id)
    for card in cards:
        await db.delete(card)


async def extract_combined(
    link_id: str, user_id: str, db: AsyncSession
) -> Dict[str, Any]:
    """
    对笔记-资料配对执行联合分析，提取常规知识点与盲点

    流程：
    1. 查询 NoteMaterialLink（校验 user_id）
    2. 计算 link_signature
    3. 命中缓存（签名匹配且 is_stale=False）则统计已有卡片数量后直接返回
    4. 缓存未命中：标记旧缓存 stale + 删除旧联合分析卡片 → 读取双方 clean_md →
       切分资料章节 → 创建联合分析会话 → 逐章节 ask（personal 笔记全文每轮都送） →
       解析 JSON 写入 KnowledgeCard
    5. 创建新的 AssessmentResult 缓存记录
    6. 返回统计字典

    Args:
        link_id: NoteMaterialLink ID
        user_id: 用户 ID
        db: 异步数据库会话

    Returns:
        Dict: 成功含 link_id/material_note_id/personal_note_id/regular_count/
              blind_spot_count/total_cards/message；失败含 error
    """
    # 1. 查询 NoteMaterialLink
    link_result = await db.execute(
        select(NoteMaterialLink).where(
            NoteMaterialLink.id == link_id,
            NoteMaterialLink.user_id == user_id,
        )
    )
    link = link_result.scalars().first()
    if not link:
        return {"error": "笔记-资料关联不存在或无权访问"}

    personal_id = link.personal_note_id
    material_id = link.material_note_id

    # 2. 计算 link_signature
    link_signature = compute_link_signature(material_id, personal_id)

    # 3. 缓存命中检查
    cached_result = await db.execute(
        select(AssessmentResult)
        .where(
            AssessmentResult.user_id == user_id,
            AssessmentResult.mode == AssessmentMode.combined_extract.value,
            AssessmentResult.link_signature == link_signature,
            AssessmentResult.is_stale == False,  # noqa: E712
        )
        .order_by(AssessmentResult.created_at.desc())
    )
    cached = cached_result.scalars().first()
    if cached:
        regular_count, blind_spot_count = await _count_combined_cards(
            db, user_id, personal_id, material_id
        )
        logger.info(
            f"联合分析命中缓存 link_id={link_id} sig={link_signature} "
            f"regular={regular_count} blind_spot={blind_spot_count}"
        )
        return {
            "link_id": link_id,
            "material_note_id": material_id,
            "personal_note_id": personal_id,
            "regular_count": regular_count,
            "blind_spot_count": blind_spot_count,
            "total_cards": regular_count + blind_spot_count,
            "message": "命中缓存，返回已有联合分析结果",
        }

    # 4. 缓存未命中：先清理旧缓存与旧卡片，再重新生成
    # 4a. 标记旧 AssessmentResult 缓存为 stale
    old_cache_result = await db.execute(
        select(AssessmentResult).where(
            AssessmentResult.user_id == user_id,
            AssessmentResult.mode == AssessmentMode.combined_extract.value,
            AssessmentResult.link_signature == link_signature,
            AssessmentResult.is_stale == False,  # noqa: E712
        )
    )
    for old in old_cache_result.scalars().all():
        old.is_stale = True

    # 4b. 删除旧联合分析卡片（仅本次 link 产生的 regular / blind_spot）
    await _delete_combined_cards(db, user_id, personal_id, material_id)
    await db.commit()

    # 4c. 读取双方笔记内容（clean_md 优先，空则 fallback 到原始 md）
    material_note = await get_note_detail(db, material_id, user_id)
    if not material_note:
        return {"error": "学习资料不存在或无权访问"}
    personal_note = await get_note_detail(db, personal_id, user_id)
    if not personal_note:
        return {"error": "用户笔记不存在或无权访问"}

    material_md = await get_clean_markdown_content(material_note) or ""
    if not material_md:
        material_md = await get_note_markdown_content(material_note) or ""
    personal_md = await get_clean_markdown_content(personal_note) or ""
    if not personal_md:
        personal_md = await get_note_markdown_content(personal_note) or ""

    if not material_md:
        return {"error": "学习资料内容为空，无法进行联合分析"}
    if not personal_md:
        return {"error": "用户笔记内容为空，无法进行联合分析"}

    # 4d. 切分资料章节
    chapters = split_into_chapters(material_md)
    if not chapters:
        return {"error": "学习资料无法切分章节"}

    # 4e. 创建联合分析会话
    llm_service = LLMService()
    session = llm_service.create_combined_analysis_session()

    regular_count = 0
    blind_spot_count = 0
    parsed_chapter_count = 0
    cards_to_add: List[KnowledgeCard] = []

    # 4f. 多轮逐章节比对（personal 笔记全文每轮都送）
    for chapter in chapters:
        chapter_title = chapter.get("chapter_title", "")
        chapter_content = chapter.get("content", "")
        if len(chapter_content) > MAX_CHAPTER_CONTENT_LEN:
            chapter_content = (
                chapter_content[:MAX_CHAPTER_CONTENT_LEN] + "\n...(内容过长已截断)"
            )

        try:
            response = await session.ask(chapter_content, personal_md)
        except Exception as e:
            logger.warning(
                f"联合分析章节 '{chapter_title}' LLM 调用失败，跳过: {e}"
            )
            continue

        # 解析 JSON 响应（容错：失败仅 warning 并跳过该轮）
        try:
            data = json.loads(response)
        except json.JSONDecodeError:
            logger.warning(
                f"联合分析章节 '{chapter_title}' JSON 解析失败，跳过该轮: "
                f"{response[:200]}"
            )
            continue

        if not isinstance(data, dict):
            logger.warning(
                f"联合分析章节 '{chapter_title}' 响应非 JSON 对象，跳过该轮"
            )
            continue

        parsed_chapter_count += 1
        resp_chapter_title = data.get("chapter_title", chapter_title) or chapter_title
        regular_points = data.get("regular_points", []) or []
        blind_spots = data.get("blind_spots", []) or []
        if not isinstance(regular_points, list):
            regular_points = []
        if not isinstance(blind_spots, list):
            blind_spots = []

        for point in regular_points:
            if not isinstance(point, dict):
                continue
            cards_to_add.append(
                _build_combined_card(
                    user_id=user_id,
                    personal_note_id=personal_id,
                    material_id=material_id,
                    point=point,
                    chapter_title=resp_chapter_title,
                    category=CardCategory.regular,
                )
            )
            regular_count += 1

        for point in blind_spots:
            if not isinstance(point, dict):
                continue
            cards_to_add.append(
                _build_combined_card(
                    user_id=user_id,
                    personal_note_id=personal_id,
                    material_id=material_id,
                    point=point,
                    chapter_title=resp_chapter_title,
                    category=CardCategory.blind_spot,
                )
            )
            blind_spot_count += 1

    # 所有章节均解析失败视为整体失败
    if parsed_chapter_count == 0:
        return {"error": "联合分析所有章节均解析失败，请稍后重试"}

    # 4g. 批量写入新卡片
    for card in cards_to_add:
        db.add(card)

    # 5. 创建新的 AssessmentResult 缓存记录
    total_cards = regular_count + blind_spot_count
    assessment = AssessmentResult(
        id=str(uuid.uuid4()),
        user_id=user_id,
        material_note_ids=[material_id],
        personal_note_ids=[personal_id],
        mode=AssessmentMode.combined_extract,
        scores={
            "regular_count": regular_count,
            "blind_spot_count": blind_spot_count,
            "total_cards": total_cards,
            "chapter_count": len(chapters),
            "parsed_chapter_count": parsed_chapter_count,
        },
        overall_score=float(total_cards),
        suggestions="",
        link_signature=link_signature,
        is_stale=False,
    )
    db.add(assessment)
    await db.commit()

    logger.info(
        f"联合分析完成 link_id={link_id} sig={link_signature} "
        f"chapters={len(chapters)} parsed={parsed_chapter_count} "
        f"regular={regular_count} blind_spot={blind_spot_count}"
    )

    return {
        "link_id": link_id,
        "material_note_id": material_id,
        "personal_note_id": personal_id,
        "regular_count": regular_count,
        "blind_spot_count": blind_spot_count,
        "total_cards": total_cards,
        "message": "联合分析完成",
    }


async def generate_extension(
    parent_card_id: str,
    user_id: str,
    db: AsyncSession,
    material_note_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    基于已掌握的父卡片生成进阶拓展知识点

    流程：
    1. 查询父卡片（校验 user_id）
    2. 校验 mastery_level >= 80，低于则返回 error 字典
    3. 准备 material_context（资料 clean_md 前 2000 字符），可选
    4. 调用 LLM 生成拓展知识点
    5. 为每个拓展知识点创建 KnowledgeCard（card_category=extension，
       parent_card_id=父卡片，note_id=父卡片 note_id，source_note_ids 继承父卡片）
    6. 返回统计字典

    Args:
        parent_card_id: 父卡片 ID
        user_id: 用户 ID
        db: 异步数据库会话
        material_note_id: 可选的关联资料 ID，用于提供上下文

    Returns:
        Dict: 成功含 parent_card_id/extension_cards/message；失败含 error
    """
    # 1. 查询父卡片
    card_result = await db.execute(
        select(KnowledgeCard).where(
            KnowledgeCard.id == parent_card_id,
            KnowledgeCard.user_id == user_id,
        )
    )
    parent = card_result.scalars().first()
    if not parent:
        return {"error": "父卡片不存在或无权访问"}

    # 2. 校验掌握度阈值
    if parent.mastery_level < EXTENSION_MASTERY_THRESHOLD:
        return {
            "error": "掌握度未达 80，无法生成拓展知识点",
            "mastery_level": parent.mastery_level,
        }

    # 3. 准备 material_context
    material_context = ""
    if material_note_id:
        material_note = await get_note_detail(db, material_note_id, user_id)
        if material_note:
            md = await get_clean_markdown_content(material_note) or ""
            if not md:
                md = await get_note_markdown_content(material_note) or ""
            if md:
                material_context = md[:EXTENSION_MATERIAL_CONTEXT_LEN]

    # 4. 调用 LLM 生成拓展知识点
    llm_service = LLMService()
    try:
        extensions = await llm_service.generate_extension_knowledge(
            card_title=parent.title,
            card_content=parent.content,
            material_context=material_context,
        )
    except Exception as e:
        logger.error(
            f"生成拓展知识点失败 (parent_card_id={parent_card_id}): {e}",
            exc_info=True,
        )
        return {"error": f"生成拓展知识点失败: {e}"}

    if not isinstance(extensions, list):
        extensions = []

    # 5. 创建拓展卡片
    created_cards: List[KnowledgeCard] = []
    for ext in extensions:
        if not isinstance(ext, dict):
            continue
        card_type = _parse_card_type(ext.get("card_type", "concept"))
        card = KnowledgeCard(
            user_id=user_id,
            note_id=parent.note_id,
            card_type=card_type,
            title=ext.get("title", "未命名拓展知识点") or "未命名拓展知识点",
            content=ext.get("content", "") or "",
            source_text=_truncate_source_text(ext.get("source_text", "") or ""),
            card_category=CardCategory.extension,
            parent_card_id=parent_card_id,
            source_note_ids=parent.source_note_ids,
        )
        db.add(card)
        created_cards.append(card)

    await db.commit()
    for card in created_cards:
        await db.refresh(card)

    # 6. 构建响应（extension_cards 序列化为 KnowledgeCardResponse）
    extension_cards = [
        KnowledgeCardResponse.model_validate(card) for card in created_cards
    ]

    logger.info(
        f"拓展知识点生成完成 parent_card_id={parent_card_id} "
        f"extension_count={len(created_cards)}"
    )

    return {
        "parent_card_id": parent_card_id,
        "extension_cards": extension_cards,
        "message": f"成功生成 {len(created_cards)} 个拓展知识点",
    }
