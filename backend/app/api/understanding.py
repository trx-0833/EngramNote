"""
AI 理解管道 API 模块

本模块提供 AI 理解管道相关的 HTTP 接口，包括触发理解、查询状态、
获取章节摘要、获取知识卡片、RAG 问答和题目生成等操作。

主要职责：
- 触发理解管道（POST /api/understanding/{note_id}/start）
- 查询理解状态（GET /api/understanding/{note_id}/status）
- 获取章节摘要（GET /api/understanding/{note_id}/chapters）
- 获取笔记知识卡片（GET /api/understanding/{note_id}/cards）
- 获取所有知识卡片（GET /api/understanding/cards）
- 获取知识卡片详情（GET /api/understanding/cards/{card_id}）
- RAG 问答（POST /api/understanding/ask）
- 触发题目生成（POST /api/understanding/{note_id}/generate-questions）
- 获取笔记题目（GET /api/understanding/{note_id}/questions）
- 获取所有题目（GET /api/understanding/questions）

设计决策：
- 所有接口需要用户认证，且只能操作自己的数据
- 只有 cleaned 或 learning_failed 状态的笔记可以触发理解
- RAG 问答跨用户所有笔记检索
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.note import Note, NoteStatus
from ..models.user import User
from ..models.knowledge_card import KnowledgeCard
from ..models.quiz_item import QuizItem
from ..api.auth import get_current_user_dependency
from ..schemas.knowledge import (
    UnderstandingStartResponse,
    UnderstandingStatusResponse,
    ChapterSummary,
    ChapterSummaryListResponse,
    KnowledgeCardResponse,
    KnowledgeCardListResponse,
    CardUpdateRequest,
    QuizItemResponse,
    QuizItemListResponse,
    QuestionRequest,
    QuestionAnswerResponse,
    AnswerSource,
    GenerateQuestionsResponse,
)
from ..services.note_service import get_note_detail
from ..services.understanding_service import detect_card_duplicates
from ..config import get_settings

settings = get_settings()
router = APIRouter()


@router.post("/{note_id}/start", response_model=UnderstandingStartResponse)
async def start_understanding(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    触发笔记理解管道

    仅对 cleaned 或 learning_failed 状态的笔记有效。
    理解管道会切分章节、生成摘要、提取知识点。
    """
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    if note.status not in (NoteStatus.cleaned, NoteStatus.learning_failed, NoteStatus.archived):
        raise HTTPException(
            status_code=400,
            detail=f"笔记当前状态为 {note.status.value}，只有 cleaned、learning_failed 或 archived 状态可以触发理解",
        )

    # 如果是 archived 状态，先删除该笔记的旧知识卡片和题目
    if note.status == NoteStatus.archived:
        # 删除旧知识卡片
        result = await db.execute(
            select(KnowledgeCard).where(KnowledgeCard.note_id == note_id)
        )
        old_cards = result.scalars().all()
        for card in old_cards:
            # 先删除关联的题目
            q_result = await db.execute(
                select(QuizItem).where(QuizItem.card_id == card.id)
            )
            old_questions = q_result.scalars().all()
            for q in old_questions:
                await db.delete(q)
            await db.delete(card)
        await db.commit()

    # 更新状态为 learning
    db_note_result = await db.execute(select(Note).where(Note.id == note_id))
    db_note = db_note_result.scalars().first()
    if db_note:
        db_note.status = NoteStatus.learning
        db_note.error_message = None
        await db.commit()

    # 触发 Celery 理解任务
    from ..tasks.understand_tasks import understand_document_task
    understand_document_task.delay(note_id)

    return UnderstandingStartResponse(
        id=note_id,
        status=NoteStatus.learning,
        message="理解任务已触发",
    )


@router.get("/{note_id}/status", response_model=UnderstandingStatusResponse)
async def get_understanding_status(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """查询笔记理解状态"""
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    return UnderstandingStatusResponse(
        id=note.id,
        status=note.status,
        error_message=note.error_message,
    )


@router.get("/{note_id}/chapters", response_model=ChapterSummaryListResponse)
async def get_chapter_summaries(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """获取笔记的章节摘要列表"""
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    # 查询该笔记的知识卡片，按章节分组
    result = await db.execute(
        select(KnowledgeCard).where(
            KnowledgeCard.note_id == note_id,
            KnowledgeCard.user_id == current_user.id,
        ).order_by(KnowledgeCard.created_at)
    )
    cards = result.scalars().all()

    # 按章节分组
    chapter_map = {}
    for card in cards:
        title = card.chapter_title or "未命名章节"
        if title not in chapter_map:
            chapter_map[title] = {
                "chapter_title": title,
                "summary": card.summary or "",
                "card_count": 0,
            }
        chapter_map[title]["card_count"] += 1

    chapters = [
        ChapterSummary(
            chapter_index=i,
            chapter_title=data["chapter_title"],
            summary=data["summary"],
            card_count=data["card_count"],
        )
        for i, data in enumerate(chapter_map.values())
    ]

    return ChapterSummaryListResponse(note_id=note_id, chapters=chapters)


@router.get("/{note_id}/cards", response_model=KnowledgeCardListResponse)
async def get_note_cards(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(999, ge=1, le=9999),
):
    """获取笔记关联的知识卡片"""
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    # 计算总数
    count_query = select(func.count()).select_from(KnowledgeCard).where(
        KnowledgeCard.note_id == note_id,
        KnowledgeCard.user_id == current_user.id,
    )
    total = (await db.execute(count_query)).scalar() or 0

    # 分页查询
    query = (
        select(KnowledgeCard)
        .where(
            KnowledgeCard.note_id == note_id,
            KnowledgeCard.user_id == current_user.id,
        )
        .order_by(KnowledgeCard.created_at)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    cards = list(result.scalars().all())

    return KnowledgeCardListResponse(
        items=[KnowledgeCardResponse.model_validate(card) for card in cards],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/cards", response_model=KnowledgeCardListResponse)
async def get_all_cards(
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(999, ge=1, le=9999),
    note_id: Optional[str] = Query(None),
):
    """获取当前用户所有知识卡片（分页）"""
    from ..models.note import Note

    # 构建查询条件
    conditions = [KnowledgeCard.user_id == current_user.id]
    if note_id:
        conditions.append(KnowledgeCard.note_id == note_id)

    # 计算总数
    count_query = select(func.count()).select_from(KnowledgeCard).where(*conditions)
    total = (await db.execute(count_query)).scalar() or 0

    # 分页查询（JOIN notes 表获取笔记标题）
    query = (
        select(KnowledgeCard, Note.title)
        .join(Note, KnowledgeCard.note_id == Note.id, isouter=True)
        .where(*conditions)
        .order_by(KnowledgeCard.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    rows = result.all()

    items = []
    for card, note_title in rows:
        item = KnowledgeCardResponse.model_validate(card)
        item.note_title = note_title or "已删除的笔记"
        items.append(item)

    return KnowledgeCardListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/cards/{card_id}", response_model=KnowledgeCardResponse)
async def get_card_detail(
    card_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """获取知识卡片详情"""
    result = await db.execute(
        select(KnowledgeCard).where(
            KnowledgeCard.id == card_id,
            KnowledgeCard.user_id == current_user.id,
        )
    )
    card = result.scalars().first()
    if not card:
        raise HTTPException(status_code=404, detail="知识卡片不存在")

    return KnowledgeCardResponse.model_validate(card)


@router.put("/cards/{card_id}", response_model=KnowledgeCardResponse)
async def update_card(
    card_id: str,
    req: CardUpdateRequest,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """编辑知识卡片的标题和内容"""
    from ..models.knowledge_card import KnowledgeCard

    result = await db.execute(
        select(KnowledgeCard).where(
            KnowledgeCard.id == card_id,
            KnowledgeCard.user_id == current_user.id,
        )
    )
    card = result.scalars().first()
    if not card:
        raise HTTPException(status_code=404, detail="知识卡片不存在")

    if req.title is not None:
        card.title = req.title
    if req.content is not None:
        card.content = req.content
    await db.commit()
    await db.refresh(card)
    return KnowledgeCardResponse.model_validate(card)


@router.delete("/cards/{card_id}", status_code=204)
async def delete_card(
    card_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """删除知识卡片及关联的题目"""
    from ..models.knowledge_card import KnowledgeCard
    from ..models.quiz_item import QuizItem

    result = await db.execute(
        select(KnowledgeCard).where(
            KnowledgeCard.id == card_id,
            KnowledgeCard.user_id == current_user.id,
        )
    )
    card = result.scalars().first()
    if not card:
        raise HTTPException(status_code=404, detail="知识卡片不存在")

    q_result = await db.execute(
        select(QuizItem).where(QuizItem.card_id == card_id)
    )
    for q in q_result.scalars().all():
        await db.delete(q)
    await db.delete(card)
    await db.commit()


@router.get("/{note_id}/duplicates")
async def get_card_duplicates(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    获取笔记的卡片去重建议

    将笔记中的每张卡片与用户所有其他卡片做 n-gram 关键词匹配，
    返回相似度过高的重复候选列表。
    """
    from ..models.knowledge_card import KnowledgeCard

    result = await db.execute(
        select(KnowledgeCard).where(
            KnowledgeCard.note_id == note_id,
            KnowledgeCard.user_id == current_user.id,
        )
    )
    cards = result.scalars().all()

    all_duplicates = []
    for card in cards:
        dupes = await detect_card_duplicates(db, current_user.id, card)
        for d in dupes:
            all_duplicates.append({
                "card_id": card.id,
                "card_title": card.title,
                **d,
            })

    return {"duplicates": all_duplicates}


@router.post("/ask", response_model=QuestionAnswerResponse)
async def ask_question(
    req: QuestionRequest,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    RAG 问答

    基于用户所有笔记的内容回答问题。
    """
    from ..services.rag_service import RAGService

    rag_service = RAGService()
    result = await rag_service.answer_question(
        question=req.question,
        user_id=current_user.id,
    )

    return QuestionAnswerResponse(
        question=req.question,
        answer=result["answer"],
        sources=[
            AnswerSource(
                note_id=s["note_id"],
                note_title=s["note_title"],
                chapter_title=s.get("chapter_title"),
                relevant_text=s["relevant_text"],
            )
            for s in result.get("sources", [])
        ],
        provider=result.get("provider", ""),
    )


@router.post("/{note_id}/generate-questions", response_model=GenerateQuestionsResponse)
async def generate_questions(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """触发题目生成"""
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    # 检查是否有知识卡片
    count_result = await db.execute(
        select(func.count()).select_from(KnowledgeCard).where(
            KnowledgeCard.note_id == note_id,
            KnowledgeCard.user_id == current_user.id,
        )
    )
    card_count = count_result.scalar() or 0

    if card_count == 0:
        raise HTTPException(status_code=400, detail="该笔记暂无知识卡片，请先触发理解管道")

    # 触发 Celery 题目生成任务
    from ..tasks.understand_tasks import generate_questions_task
    generate_questions_task.delay(note_id)

    return GenerateQuestionsResponse(
        note_id=note_id,
        message="题目生成任务已触发",
        question_count=0,
    )


@router.get("/{note_id}/questions", response_model=QuizItemListResponse)
async def get_note_questions(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """获取笔记关联的题目"""
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    # 计算总数
    count_query = select(func.count()).select_from(QuizItem).where(
        QuizItem.note_id == note_id,
        QuizItem.user_id == current_user.id,
    )
    total = (await db.execute(count_query)).scalar() or 0

    # 分页查询
    query = (
        select(QuizItem)
        .where(
            QuizItem.note_id == note_id,
            QuizItem.user_id == current_user.id,
        )
        .order_by(QuizItem.created_at)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    questions = list(result.scalars().all())

    items = []
    for q in questions:
        item = QuizItemResponse.model_validate(q)
        item.note_title = note.title
        items.append(item)

    return QuizItemListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/questions", response_model=QuizItemListResponse)
async def get_all_questions(
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    note_id: Optional[str] = Query(None),
):
    """获取当前用户所有题目（分页）"""
    conditions = [QuizItem.user_id == current_user.id]
    if note_id:
        conditions.append(QuizItem.note_id == note_id)

    # 计算总数
    count_query = select(func.count()).select_from(QuizItem).where(*conditions)
    total = (await db.execute(count_query)).scalar() or 0

    # 分页查询，JOIN notes 表获取 note_title
    query = (
        select(QuizItem, Note.title.label("note_title"))
        .outerjoin(Note, QuizItem.note_id == Note.id)
        .where(*conditions)
        .order_by(QuizItem.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    rows = result.all()

    items = []
    for quiz, note_title in rows:
        item = QuizItemResponse.model_validate(quiz)
        item.note_title = note_title or "已删除的笔记"
        items.append(item)

    return QuizItemListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )
