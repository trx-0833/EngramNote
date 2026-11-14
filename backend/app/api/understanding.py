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
- 卡片查重建议（GET /api/understanding/{note_id}/duplicates）

设计决策：
- 所有接口需要用户认证，且只能操作自己的数据
- 只有 cleaned 或 learning_failed 状态的笔记可以触发理解
- RAG 问答跨用户所有笔记检索

## SSE 事件契约（阶段 5.1：`POST /ask/stream` 为什么**不写** `response_model`）

与 `app/api/notes/ask.py` 的 `/{note_id}/ask/stream` 是同一回事，也是同一个结论：

- `response_model=` 描述的是"一个 JSON 文档"，而本端点返回的是
  **`text/event-stream` 的帧序列**。处理函数返回 `StreamingResponse`，
  套上 `response_model` 会导致校验失败或**被包装成一次性 `JSONResponse`**
  —— 后者直接破坏流式行为，属于禁止项。
- OpenAPI 3.1 能声明 `text/event-stream` 这个 media type，
  但**没有**表达"事件名 → data 结构"的能力。硬塞 JSON schema 只会得到
  "看着有类型、实际对不上"的契约。

因此做法是：用 `EventStreamResponse` 如实声明 media type
（此前 schema 里它被标成 `application/json` + 空 schema，是**错的**），
并把事件契约写在下面。

事件契约（`\n\n` 分隔帧，`data` 均为 JSON）：

| event     | data                                                         | 出现时机 |
|-----------|--------------------------------------------------------------|----------|
| `meta`    | `{"retrieval_status": "<状态>", "provider": "<提供商>"}`       | 首个事件（检索阶段结束、LLM 开始之前） |
| `token`   | `{"content": "<片段>"}`                                        | 每个流式片段一个 |
| `sources` | `{"sources": [<AnswerSource 数组>], "provider": "<提供商>"}`   | 所有 token 之后、`done` 之前 |
| `done`    | `{}`                                                          | 正常结束 |
| `error`   | `{"message": "<原因>"}`，部分分支另有 `error_code`             | 校验失败或流中断，**之后流即结束** |

⚠️ `sources` 事件里的单个 source 结构**就是** `schemas.knowledge.AnswerSource`
（`note_id` / `note_title` / `chapter_title` / `relevant_text` / 定位字段），
它是本端点唯一能被 OpenAPI 复用的部分 —— 但它藏在 SSE 帧里，schema 仍然看不到。
`error` 事件同样有两种形态：空问题时带 `error_code=EMPTY_QUESTION`，
未预期异常时**只有** `message`。
"""

import asyncio
import json
import logging
import time
from typing import Optional, List

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, delete as sql_delete, or_, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import i18n
from ..core.app_error import (
    CARD_NOT_FOUND,
    EMPTY_QUESTION,
    classify_exception,
    NOTE_NOT_FOUND,
    NOTE_STATUS_INVALID,
    QA_SESSION_NOT_FOUND,
    QA_SESSION_SURFACE_MISMATCH,
    QUIZ_OPTIONS_REQUIRED,
    REVIEW_QUIZ_NOT_FOUND,
    UNDERSTANDING_IN_PROGRESS,
    UNDERSTANDING_NO_CARDS,
    AppError,
)
from ..database import get_db
from ..models.note import Note, NoteStatus
from ..models.user import User
from ..models.knowledge_card import KnowledgeCard
from ..models.quiz_item import QuizItem, QuestionType
from ..models.review_log import ReviewLog

# B5：删除题目要级联清 review_states（它没有外键，数据库不会替你做）
from ..models.review_state import ITEM_TYPE_QUIZ, ReviewState
from ..models.card_relation import CardRelation
from ..api.auth import get_current_user_dependency
from ..models.qa_history import SURFACE_GLOBAL, TURN_STATUS_ABORTED, TURN_STATUS_FAILED, TURN_STATUS_OK
from ..services import qa_history_service as qa_service
from ..services.qa_context import assemble_messages, build_retrieval_query, context_turns_used, usable_history
from ..schemas.common import EventStreamResponse
from ..schemas.knowledge import (
    UnderstandingStartRequest,
    UnderstandingImpact,
    UnderstandingStartResponse,
    UnderstandingStatusResponse,
    ChapterSummary,
    ChapterSummaryListResponse,
    KnowledgeCardResponse,
    KnowledgeCardListResponse,
    CardUpdateRequest,
    CardDuplicateListResponse,
    QuizItemResponse,
    QuizItemListResponse,
    QuizItemUpdateRequest,
    QuestionRequest,
    QuestionAnswerResponse,
    AnswerSource,
    GenerateQuestionsResponse,
)
from ..services.note_service import get_note_detail
from ..services.understanding_service import detect_card_duplicates
from ..services.rag_service import RAGService
from ..services.llm_service import LLMService
from ..tasks.dispatch import dispatch
from ..tasks.understand_tasks import understand_document_task, generate_questions_task
from ..config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/{note_id}/start", response_model=UnderstandingStartResponse)
async def start_understanding(
    note_id: str,
    req: UnderstandingStartRequest = UnderstandingStartRequest(),
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    触发笔记理解管道

    仅对 cleaned / learning_failed / archived 状态的笔记有效。
    archived 笔记重新理解会清空全部旧产物（卡片/题目/复习记录/图谱关系），
    必须先以 confirm=false 调用获取影响数量，用户确认后带 confirm=true 再次调用（见 docs/decisions.md#F-02）。
    """
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise AppError(NOTE_NOT_FOUND, "笔记不存在", 404)

    if note.status == NoteStatus.learning:
        # 进行中禁止重复触发，见 docs/decisions.md#F-29
        raise AppError(
            UNDERSTANDING_IN_PROGRESS,
            "理解任务正在进行中，请等待完成",
            409,
        )

    if note.status not in (NoteStatus.cleaned, NoteStatus.learning_failed, NoteStatus.archived):
        raise AppError(
            NOTE_STATUS_INVALID,
            f"笔记当前状态为 {note.status.value}，只有 cleaned、learning_failed 或 archived 状态可以触发理解",
            400,
            params={"status": note.status.value},
        )

    # archived 笔记未确认时，只返回影响数量，不执行任何删除（见 docs/decisions.md#F-02）
    if note.status == NoteStatus.archived and not req.confirm:
        impact = await _count_understanding_impact(db, note_id)
        return UnderstandingStartResponse(
            id=note_id,
            status=note.status,
            message=i18n.localize(
                "重新理解将删除该笔记现有的全部学习成果（卡片、题目、复习记录、图谱关系），确认后不可恢复",
                "Re-running understanding deletes all existing learning results for this note "
                "(cards, questions, review history, graph relations). This cannot be undone.",
            ),
            requires_confirm=True,
            impact=impact,
        )

    # 重新学习前清空旧产物（无论当前状态），避免重复卡片
    # 批量删除关联的复习记录
    quiz_ids_result = await db.execute(select(QuizItem.id).where(QuizItem.note_id == note_id))
    quiz_ids = [row[0] for row in quiz_ids_result.all()]
    if quiz_ids:
        await db.execute(sql_delete(ReviewLog).where(ReviewLog.quiz_id.in_(quiz_ids)))
    # 批量删除关联的卡片关系
    card_ids_result = await db.execute(select(KnowledgeCard.id).where(KnowledgeCard.note_id == note_id))
    card_ids = [row[0] for row in card_ids_result.all()]
    if card_ids:
        await db.execute(
            sql_delete(CardRelation).where(
                or_(
                    CardRelation.card_id_1.in_(card_ids),
                    CardRelation.card_id_2.in_(card_ids),
                )
            )
        )
    # ★ 断掉"别的笔记引用本篇卡片"的链（2026-10-22，拍板 (b)：置空而非删除）
    #
    # 为什么必须在删卡片**之前**做：`quiz_items.card_id` 与 `review_logs.card_id`
    # 都是指向 `knowledge_cards.id` 的外键，而题目/复习记录**可以引用别的笔记的
    # 卡片**。下面两行按 `note_id` 删，删不到那些跨笔记的行 ⇒ 删卡片时撞外键 ⇒ 500。
    #
    # 实测：全库 8 条跨笔记题目、3 篇笔记重跑必 500（见
    # `docs/prompt-version-comparison-2026-10-22.md` §4）。
    #
    # 置空而不是删除：那些行在**别的笔记**里，删掉就是"重跑 A 笔记导致 B 笔记少题"。
    # 与 `review_log.card_id` 的既有设计同一手法（`models/review_log.py:79-81`）——
    # 卡片会被重建，题目/复习记录不该跟着消失。
    if card_ids:
        await db.execute(
            sql_update(QuizItem).where(QuizItem.card_id.in_(card_ids), QuizItem.note_id != note_id).values(card_id=None)
        )
        await db.execute(
            # ⚠️ 这里**不加** `note_id != note_id` 过滤：`review_logs.note_id` 可空，
            #    而 `NULL != 'x'` 求值为 NULL（不是 true）⇒ 那些行会被漏掉、
            #    照样挡住删除。而且属于**本篇**题目的复习记录在 `:172` 已经删掉了，
            #    所以此刻还引用这些卡片的行，本来就是"别处来的"。
            sql_update(ReviewLog).where(ReviewLog.card_id.in_(card_ids)).values(card_id=None)
        )
    # 批量删除关联题目和知识卡片
    await db.execute(sql_delete(QuizItem).where(QuizItem.note_id == note_id))
    await db.execute(sql_delete(KnowledgeCard).where(KnowledgeCard.note_id == note_id))
    await db.commit()

    # 更新状态为 learning
    note.status = NoteStatus.learning
    note.error_message = None
    await db.commit()
    await db.refresh(note)
    # 状态写穿镜像
    from ..services.vault_meta import write_note_meta

    write_note_meta(note)

    # 触发 Celery 理解任务
    dispatch(understand_document_task, note_id)

    return UnderstandingStartResponse(
        id=note_id,
        status=NoteStatus.learning,
        message=i18n.localize("理解任务已触发", "Understanding task started"),
    )


async def _count_understanding_impact(db: AsyncSession, note_id: str) -> UnderstandingImpact:
    """统计重新理解将删除的旧产物数量（见 docs/decisions.md#F-02）"""
    from sqlalchemy import func as sa_func

    cards = (
        await db.execute(select(sa_func.count()).select_from(KnowledgeCard).where(KnowledgeCard.note_id == note_id))
    ).scalar() or 0

    quizzes = (
        await db.execute(select(sa_func.count()).select_from(QuizItem).where(QuizItem.note_id == note_id))
    ).scalar() or 0

    review_logs = (
        await db.execute(select(sa_func.count()).select_from(ReviewLog).where(ReviewLog.note_id == note_id))
    ).scalar() or 0

    relations = 0
    card_ids_result = await db.execute(select(KnowledgeCard.id).where(KnowledgeCard.note_id == note_id))
    card_ids = [row[0] for row in card_ids_result.all()]
    if card_ids:
        relations = (
            await db.execute(
                select(sa_func.count())
                .select_from(CardRelation)
                .where(
                    or_(
                        CardRelation.card_id_1.in_(card_ids),
                        CardRelation.card_id_2.in_(card_ids),
                    )
                )
            )
        ).scalar() or 0

    return UnderstandingImpact(
        cards=cards,
        quizzes=quizzes,
        review_logs=review_logs,
        relations=relations,
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
        raise AppError(NOTE_NOT_FOUND, "笔记不存在", 404)

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
        raise AppError(NOTE_NOT_FOUND, "笔记不存在", 404)

    # 查询该笔记的知识卡片，按章节分组
    result = await db.execute(
        select(KnowledgeCard)
        .where(
            KnowledgeCard.note_id == note_id,
            KnowledgeCard.user_id == current_user.id,
        )
        .order_by(KnowledgeCard.created_at)
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
        raise AppError(NOTE_NOT_FOUND, "笔记不存在", 404)

    # 计算总数
    count_query = (
        select(func.count())
        .select_from(KnowledgeCard)
        .where(
            KnowledgeCard.note_id == note_id,
            KnowledgeCard.user_id == current_user.id,
        )
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
    keyword: Optional[str] = Query(None, description="搜索关键词，匹配标题或内容"),
):
    """获取当前用户所有知识卡片（分页），支持关键词搜索"""
    # 构建查询条件
    conditions = [KnowledgeCard.user_id == current_user.id]
    # 回收站笔记的卡片暂不可见（note_id 为 NULL 的独立/提升卡片保留）
    conditions.append(Note.not_trashed(KnowledgeCard.note_id))
    if note_id:
        conditions.append(KnowledgeCard.note_id == note_id)
    if keyword:
        # 转义 SQL 通配符（见 docs/decisions.md#F-32）
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(
            or_(
                KnowledgeCard.title.ilike(f"%{escaped}%", escape="\\"),
                KnowledgeCard.content.ilike(f"%{escaped}%", escape="\\"),
            )
        )

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
        # note_id 为 NULL 的是独立/提升卡片；note_id 指向已物理删除笔记的为悬挂引用
        item.note_title = note_title or ("独立卡片" if card.note_id is None else "已删除的笔记")
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
        raise AppError(CARD_NOT_FOUND, "知识卡片不存在", 404)

    # JOIN Note 表获取笔记标题（note_id 为 NULL 的是独立/提升卡片）
    note_title = None
    if card.note_id is not None:
        note_result = await db.execute(select(Note.title).where(Note.id == card.note_id))
        note_title_row = note_result.first()
        note_title = note_title_row[0] if note_title_row else "已删除的笔记"
    else:
        note_title = "独立卡片"

    item = KnowledgeCardResponse.model_validate(card)
    item.note_title = note_title
    return item


@router.put("/cards/{card_id}", response_model=KnowledgeCardResponse)
async def update_card(
    card_id: str,
    req: CardUpdateRequest,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """编辑知识卡片的标题和内容"""
    result = await db.execute(
        select(KnowledgeCard).where(
            KnowledgeCard.id == card_id,
            KnowledgeCard.user_id == current_user.id,
        )
    )
    card = result.scalars().first()
    if not card:
        raise AppError(CARD_NOT_FOUND, "知识卡片不存在", 404)

    if req.title is not None:
        card.title = req.title
    if req.content is not None:
        card.content = req.content
    await db.commit()
    await db.refresh(card)

    # JOIN Note 表获取笔记标题（note_id 为 NULL 的是独立/提升卡片）
    note_title = None
    if card.note_id is not None:
        note_result = await db.execute(select(Note.title).where(Note.id == card.note_id))
        note_title_row = note_result.first()
        note_title = note_title_row[0] if note_title_row else "已删除的笔记"
    else:
        note_title = "独立卡片"

    item = KnowledgeCardResponse.model_validate(card)
    item.note_title = note_title
    return item


@router.delete("/cards/{card_id}", status_code=204)
async def delete_card(
    card_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """删除知识卡片及关联的题目、复习记录和图谱关系"""
    result = await db.execute(
        select(KnowledgeCard).where(
            KnowledgeCard.id == card_id,
            KnowledgeCard.user_id == current_user.id,
        )
    )
    card = result.scalars().first()
    if not card:
        raise AppError(CARD_NOT_FOUND, "知识卡片不存在", 404)

    # 删除关联的卡片关系
    await db.execute(
        sql_delete(CardRelation).where(
            or_(
                CardRelation.card_id_1 == card_id,
                CardRelation.card_id_2 == card_id,
            )
        )
    )

    # 解除子卡片的父级引用（parent_card_id 自引用外键为 NO ACTION，需先置 NULL）
    await db.execute(
        sql_update(KnowledgeCard).where(KnowledgeCard.parent_card_id == card_id).values(parent_card_id=None)
    )

    # 删除关联的复习记录
    quiz_ids_result = await db.execute(select(QuizItem.id).where(QuizItem.card_id == card_id))
    quiz_ids = [row[0] for row in quiz_ids_result.all()]
    if quiz_ids:
        await db.execute(sql_delete(ReviewLog).where(ReviewLog.quiz_id.in_(quiz_ids)))

    # 删除关联的题目（Core 批量删除立即执行，确保先于卡片本身的 DELETE，
    # 避免 ORM flush 排序不确定导致 quiz_items 外键约束失败）
    await db.execute(sql_delete(QuizItem).where(QuizItem.card_id == card_id))
    await db.delete(card)
    await db.commit()


@router.get("/{note_id}/duplicates", response_model=CardDuplicateListResponse)
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
            all_duplicates.append(
                {
                    "card_id": card.id,
                    "card_title": card.title,
                    **d,
                }
            )

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
    # 拒绝空/纯空白问题，避免无意义地消耗一次 LLM 调用（见 docs/decisions.md#F-31）
    if not req.question or not req.question.strip():
        raise AppError(EMPTY_QUESTION, "问题不能为空", 422)

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
                # 定位字段（阶段 2.7）：漏传任何一个，前端就只能"显示来源"
                # 而不能"跳到原文"，且不会有报错 —— 属于静默降级
                chunk_id=s.get("chunk_id"),
                chunk_index=s.get("chunk_index"),
                char_start=s.get("char_start"),
                char_end=s.get("char_end"),
                heading_path=s.get("heading_path"),
                line_start=s.get("line_start"),
                line_end=s.get("line_end"),
            )
            for s in result.get("sources", [])
        ],
        provider=result.get("provider", ""),
        retrieval_status=result.get("retrieval_status", ""),
        no_context=bool(result.get("no_context", False)),
    )


@router.post("/ask/stream", response_class=EventStreamResponse)
async def ask_question_stream(
    req: QuestionRequest,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    RAG 问答流式接口（SSE）

    通过 Server-Sent Events 逐 token 返回 LLM 生成的回答，
    适合前端实时展示生成过程。

    流程：
    1. **解析/创建会话，读出最近 N 轮成功问答**（多轮，2026-11-13）
    2. 调用 rag_service.retrieve_context() 完成检索阶段（不调用 LLM）；
       追问时的检索词是"上一问 + 当前问"拼接的（否则"那它呢"检索不到）
    3. 首事件下发 retrieval_status / provider / session_id / history_used
    4. 复用与 rag_answer() 一致的 system prompt 构建 messages
       （有历史时在末尾追加"事实以本轮资料为准"的硬指令）
    5. 调用 llm_service.chat_stream() 流式生成回答
    6. 流式结束后发送 sources 与 done 事件，**并把这一轮落库**（成功/失败/中止三种都落）

    SSE 事件格式：
    - event: meta    data: {"retrieval_status": "...", "provider": "...", "session_id": "...", "history_used": 2}
    - event: token   data: {"content": "..."}                每个 token 片段
    - event: sources data: {"sources": [...], "provider": "..."}  流式结束后返回引用来源
    - event: done    data: {}                                结束标记
    - event: error   data: {"message": "..."}                异常情况
    """
    settings = get_settings()
    history_enabled = bool(getattr(settings, "qa_history_enabled", True))
    multiturn_enabled = history_enabled and bool(getattr(settings, "qa_multiturn_enabled", True))

    # 三轮预算的读取**不能用 `x or 默认值`**（2026-11-13 第二轮审计 MINOR-2）：
    # `or` 会把显式配置的 `0` 当成"没配"，于是 `QA_CONTEXT_MAX_TURNS=0`
    # （最自然的"不要带历史"写法）反而带上 6 轮，配置与行为相反且没有任何日志。
    # 现在 0 是有意义的取值，语义写在这里：
    #   - `max_turns=0`     → 不带任何历史（`assemble_messages` 天然支持）
    #   - `answer_chars=0`  → 单条答案不截断（`_clip` 对 `limit<=0` 原样返回）
    #   - `max_chars=0`     → 历史总量不限（`assemble_messages` 里对预算做 `>0` 判断）
    def _int_setting(name: str, default: int) -> int:
        value = getattr(settings, name, default)
        return default if value is None else int(value)

    max_turns = _int_setting("qa_context_max_turns", 6)
    answer_chars = _int_setting("qa_context_answer_chars", 400)
    max_chars = _int_setting("qa_context_max_chars", 4000)

    # 拒绝空/纯空白问题 —— 必须在**建会话之前**（2026-11-13 审计 m3）：
    # 原来这条校验在生成器里，于是一次 `{"question": ""}` 已经在库里留下一条
    # `turn_count=0` 的"未命名对话"，而用户没有办法只删一条空会话。
    if not req.question or not req.question.strip():
        raise AppError(EMPTY_QUESTION, "问题不能为空", 422)

    # ── 会话解析放在**流开始之前** ──
    #
    # 这样"会话已被删除"能以真正的 HTTP 404 返回（前端 `askQuestionStream` 抛 ApiError，
    # 据 error_code 清掉 ?s= 并提示）。放进生成器里就只能发一个 error 帧，
    # 而那时候响应的状态码已经发出去了，客户端得自己再解析一遍。
    #
    # `surface=SURFACE_GLOBAL` 是**校验**而不只是过滤（审计 M1）：拿一个笔记浮层会话的
    # id 来全库问答，会被判成"取不到"→ 404，而不是把 global 轮次写进 note 会话。
    session = None
    history: List[tuple] = []
    if history_enabled:
        if req.session_id:
            session = await qa_service.get_or_create_session(
                db, user_id=current_user.id, session_id=req.session_id, surface=SURFACE_GLOBAL
            )
            if session is None:
                # 区分"真的没了"与"入口不对"（第二轮审计 MINOR-4）：两者都是 404
                # （不泄露存在性），但码不同 —— 否则把一个仍在笔记浮层里的会话
                # 说成"已被删除"，用户会去找备份。见 `get_session_by_id` 的说明。
                still_there = await qa_service.get_session_by_id(db, user_id=current_user.id, session_id=req.session_id)
                if still_there is not None:
                    raise AppError(QA_SESSION_SURFACE_MISMATCH, "这个对话属于另一个入口", 404)
                raise AppError(QA_SESSION_NOT_FOUND, "对话不存在或已被删除", 404)
        else:
            session = await qa_service.get_or_create_session(
                db, user_id=current_user.id, surface=SURFACE_GLOBAL, first_question=req.question
            )

        if session is not None and multiturn_enabled:
            turns = await qa_service.recent_ok_turns(
                db, session_id=session.id, user_id=current_user.id, limit=max_turns
            )
            history = usable_history([(t.question, t.answer, t.status) for t in turns])

        # 把连接还回去（审计 m7）：`database.py` 写明流式端点会在**整个流期间**持有
        # 请求作用域的 session，而这里读完之后只剩生成器里的落库（用的是独立 session）。
        # 只读事务的提交没有副作用，但能让一条连接在几十秒的生成期间不被占住。
        await db.commit()

    #: 追问时的检索词 = 上一问 + 当前问（计划 §4.3）
    prev_question = history[-1][0] if history else None
    retrieval_query = build_retrieval_query(prev_question, req.question)
    #: 本轮**实际**带上的轮数。审计 m1：`len(history)` 是"想带几轮"，超预算时会被裁掉，
    #: 落库与 `meta` 都必须用真实值，否则这个排障信号会撒谎。
    context_turns = context_turns_used(
        history=history, max_turns=max_turns, answer_chars=answer_chars, max_chars=max_chars
    )
    session_id = session.id if session is not None else None

    async def _persist_completed_turn(
        *,
        status: str,
        answer_text: str,
        error_code: Optional[str] = None,
        provider: Optional[str] = None,
        sources: Optional[list] = None,
        retrieval_status: Optional[str] = None,
        latency_ms: Optional[int] = None,
    ) -> None:
        """用一个**独立的 DB 会话**落库

        ⚠️ 不复用请求作用域的 `db`：流式响应的生成器可能比依赖的生命周期活得更久
        （客户端慢速读取、或断开后仍在收尾），而 `Depends(get_db)` 会在响应结束时关闭会话。
        独立会话让"写历史"与"请求生命周期"解耦，这也是能安全处理 `CancelledError` 的前提。
        """
        if not history_enabled or session_id is None:
            return
        # 落库逻辑在服务层（第二轮审计 NIT-1）：`surface` 必填，两个端点各写一份
        # 时已经分叉过一次（默认值把 note 会话过滤掉 → 静默不落库）。
        await qa_service.persist_turn(
            user_id=current_user.id,
            session_id=session_id,
            surface=SURFACE_GLOBAL,
            question=req.question,
            answer=answer_text,
            status=status,
            error_code=error_code,
            provider=provider,
            retrieval_status=retrieval_status,
            retrieval_query=retrieval_query,
            sources=sources,
            context_turns=context_turns,
            latency_ms=latency_ms,
        )

    async def event_stream():
        # fallback 分支会改写检索词（审计 m2），所以先声明（`nonlocal` 必须在**任何读之前**）
        nonlocal retrieval_query
        answer_parts: list[str] = []
        started_at = time.monotonic()
        try:
            # 1. 检索阶段（不调用 LLM）。追问时用拼接后的检索词。
            rag_service = RAGService()
            retrieval = await rag_service.retrieve_context(
                question=retrieval_query,
                user_id=current_user.id,
            )
            context = retrieval["context"]
            sources = retrieval["sources"]
            provider = retrieval["provider"]
            retrieval_status = retrieval.get("retrieval_status", "")

            # 1b. 追问时若拼接检索为空，退回只用当前问再检一次（计划 §4.3）。
            #     两次都空就如实回答"没有找到相关片段"，不编造。
            # 语料为空时不要再检索一次（第二轮审计 MINOR-8）：新用户 / 还没补向量时
            # "拼接词没命中"是必然的，第二遍同样必然为空 —— 白跑一次 embedding + 向量检索。
            if not sources and prev_question and not retrieval.get("corpus_empty"):
                retry = await rag_service.retrieve_context(
                    question=req.question,
                    user_id=current_user.id,
                )
                if retry.get("sources"):
                    retrieval = retry
                    context = retry["context"]
                    sources = retry["sources"]
                    provider = retry["provider"]
                    retrieval_status = retry.get("retrieval_status", "")
                    # 记下**真正**用过的查询词（审计 m2）：`retrieval_query` 这一列存在的
                    # 唯一理由就是归因"这轮为什么没检索到"，回退了却仍记拼接词等于自相矛盾。
                    retrieval_query = req.question

            # 2. 首事件下发检索降级状态 + 会话（前端据此写 ?s= 与提示"参考了 N 轮上文"）
            yield f"event: meta\ndata: {json.dumps({'retrieval_status': retrieval_status, 'provider': provider, 'session_id': session_id, 'history_used': context_turns}, ensure_ascii=False)}\n\n"

            # 3. 构建 messages（有历史时把最近几轮插在 system 与当前问之间）
            llm_service = LLMService()
            if not context.strip():
                system_prompt = (
                    "你是一个知识渊博的学习助手。用户的问题没有在 TA 的笔记中找到相关信息，"
                    "请用你自己的知识来回答这个问题。回答时请说明这是基于你的通用知识。"
                )
                user_content = req.question
            else:
                system_prompt = (
                    "你是一个知识渊博的学习助手。请根据提供的参考资料回答用户问题。\n\n"
                    "回答原则：\n"
                    "1. 优先使用参考资料：如果参考资料中包含相关信息，请以其为主要依据\n"
                    "2. 自主知识补充：如果参考资料不足或没有相关信息，可以结合你自己的知识来回答，"
                    "但请说明这部分是基于你的知识补充的\n"
                    "3. 诚实标注：如果回答中既有参考资料的内容，也有你自己的知识，请尽量区分\n"
                    "4. 回答应详细有用：不要简单地回复没有相关信息，而是尽力提供有价值的回答\n"
                    "5. 适当引用：回答中可引用参考资料中的原文来增强可信度"
                )
                user_content = f"参考资料：\n{context}\n\n问题：{req.question}"

            messages = assemble_messages(
                system_prompt=system_prompt,
                current_user_content=user_content,
                history=history,
                max_turns=max_turns,
                answer_chars=answer_chars,
                max_chars=max_chars,
            )

            # 4. 流式输出 token（累积起来供落库：中止时也要保留已生成的部分）
            async for chunk in llm_service.chat_stream(messages, scene="rag_answer_stream"):
                answer_parts.append(chunk)
                yield f"event: token\ndata: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"

            # 5. 发送 sources 事件
            yield f"event: sources\ndata: {json.dumps({'sources': sources, 'provider': provider}, ensure_ascii=False)}\n\n"

            # 6. 发送 done 事件
            yield "event: done\ndata: {}\n\n"

            # 7. 落库（成功）
            await _persist_completed_turn(
                status=TURN_STATUS_OK,
                answer_text="".join(answer_parts),
                provider=provider,
                sources=sources,
                retrieval_status=retrieval_status,
                latency_ms=int((time.monotonic() - started_at) * 1000),
            )
        except asyncio.CancelledError:
            # 客户端断开 / 用户点「停止生成」：**这一轮最需要回看**，所以用 shield 写库
            # （裸 await 会被取消立即打断，结果是"点了停止就什么都不留"）
            await asyncio.shield(
                _persist_completed_turn(
                    status=TURN_STATUS_ABORTED,
                    answer_text="".join(answer_parts),
                    latency_ms=int((time.monotonic() - started_at) * 1000),
                )
            )
            raise
        except Exception as e:
            logger.warning(f"SSE 流式问答失败: {e}", exc_info=True)
            # 只发稳定错误码 + 固定中文文案（第二轮审计 MINOR-5）：原来把 `str(e)` 原样下发，
            # SQLAlchemy 的 OperationalError 文本里带 SQL 与绑定参数 —— DB 故障时等于把
            # 内部结构送到界面上。详细异常只进日志（上面那行 warning 已带 exc_info）。
            yield f"event: error\ndata: {json.dumps({'message': '生成回答时出错，请稍后重试', 'error_code': classify_exception(e)}, ensure_ascii=False)}\n\n"
            await _persist_completed_turn(
                status=TURN_STATUS_FAILED,
                answer_text="".join(answer_parts),
                error_code=classify_exception(e),
                latency_ms=int((time.monotonic() - started_at) * 1000),
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{note_id}/generate-questions", response_model=GenerateQuestionsResponse)
async def generate_questions(
    note_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
    target_categories: Optional[List[str]] = Query(None, description="目标卡片类别，如 blind_spot/extension"),
    target_difficulty: Optional[str] = Query(None, description="目标难度倾向"),
    prompt_version: Optional[str] = Query(
        None,
        description="出题提示词版本（1/2/3）。留空用服务端默认版；A3′ 的三版对比靠它",
    ),
):
    """触发题目生成

    ## `prompt_version`（A3′，2026-10-22）

    出题提示词有**三个并存版本**（`services/llm/prompts.py`）：
    v1 = 只有选择题/只有 easy（改造前的行为，仍是服务端默认）、
    v2 = 题型与难度可配、v3 = v2 + 出题质量规则。

    它们同时存在而不是"改一次文本"的原因有两个：① 改了默认版就再也拿不到
    旧版的产物，**对比实验做不了**；② `prompt_version` 会按版本分组
    （`PROMPT_VERSIONS`），分组报表才可能回答"新版是不是更好"。

    ⚠️ 版本号写错时**不报错**、退回服务端默认版（见
    `prompts.question_session_prompt` 的说明）—— 出题是长任务，
    为参数写错整篇失败代价太大。所以这里也不做枚举校验。
    """
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise AppError(NOTE_NOT_FOUND, "笔记不存在", 404)

    # 检查是否有知识卡片
    count_result = await db.execute(
        select(func.count())
        .select_from(KnowledgeCard)
        .where(
            KnowledgeCard.note_id == note_id,
            KnowledgeCard.user_id == current_user.id,
        )
    )
    card_count = count_result.scalar() or 0

    if card_count == 0:
        raise AppError(UNDERSTANDING_NO_CARDS, "该笔记暂无知识卡片，请先触发理解管道", 400)

    # 触发 Celery 题目生成任务（透传定向出题参数 + 提示词版本）
    dispatch(generate_questions_task, note_id, target_categories, target_difficulty, prompt_version)

    return GenerateQuestionsResponse(
        note_id=note_id,
        message=i18n.localize("题目生成任务已触发", "Question generation started"),
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
        raise AppError(NOTE_NOT_FOUND, "笔记不存在", 404)

    # 计算总数
    count_query = (
        select(func.count())
        .select_from(QuizItem)
        .where(
            QuizItem.note_id == note_id,
            QuizItem.user_id == current_user.id,
        )
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
    page_size: int = Query(20, ge=1, le=1000),
    note_id: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None, description="搜索关键词，匹配题目内容"),
):
    """获取当前用户所有题目（分页），支持关键词搜索

    ## `page_size` 上限 2026-10-21 由 100 放宽到 1000

    起因：真实用户走查发现 `/questions` 要 4.8 秒才出内容。
    实测该用户 **3367 道题**，而前端为了"按笔记分组"必须把题目**全部取回**，
    于是按 100/页 要打 **34 次**请求。

    单次请求实测 76~126 ms，且后端是**单进程 uvicorn + SQLite**——
    并发只是把网络往返重叠掉，服务端仍近乎串行，所以**唯一有效的杠杆是
    减少往返次数**：34 次 → 4 次。

    放宽而不是新开"取全部"接口的理由：这是**纯粹的边界放宽**，
    对既有调用方（`page_size<=100`）行为逐字节不变，也不需要新的响应契约。
    """
    conditions = [QuizItem.user_id == current_user.id]
    # 回收站笔记的题目暂不可见（note_id 为 NULL 的悬挂/提升题目保留）
    conditions.append(Note.not_trashed(QuizItem.note_id))
    if note_id:
        conditions.append(QuizItem.note_id == note_id)
    if keyword:
        # 转义 SQL 通配符（见 docs/decisions.md#F-32）
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(QuizItem.question.ilike(f"%{escaped}%", escape="\\"))

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
        # note_id 为 NULL：独立/提升卡片悬挂的题目；否则为笔记被物理删除
        item.note_title = note_title or ("独立卡片" if quiz.note_id is None else "已删除的笔记")
        items.append(item)

    return QuizItemListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


async def _load_quiz_for_user(db: AsyncSession, quiz_id: str, user_id: str) -> QuizItem:
    """按 id + 归属人取一道题；不存在/不属于当前用户都报同一个 404

    ## 为什么"不属于你"也报 404 而不是 403

    403 会泄露"这个 id 确实存在"这一位信息。与 `cards/{card_id}` 同一口径 ——
    把"存在性"与"归属"合成一个回答没有任何代价，分成两种状态反而要多一组
    用例去守它。
    """
    result = await db.execute(
        select(QuizItem).where(
            QuizItem.id == quiz_id,
            QuizItem.user_id == user_id,
        )
    )
    quiz = result.scalars().first()
    if not quiz:
        raise AppError(REVIEW_QUIZ_NOT_FOUND, "题目不存在", 404)
    return quiz


def _normalized_options(quiz_type: QuestionType, options: Optional[List[str]]) -> Optional[str]:
    """把选项列表序列化成库里存的 JSON 字符串（**非选择题一律存 None**）

    为什么非选择题要丢掉选项：答题与判分路径按 `question_type` 分流
    （`sm2_service` 的 `_grade_choice` / `_grade_fill_blank` / 自评），
    非选择题上的选项**不会被任何代码读到**。留着它只会在导出、展示、
    以及将来的数据清理里冒出噪音 —— 而"存了但永远不读"的字段正是
    这个仓库反复清理的那类东西。
    """
    if quiz_type != QuestionType.choice:
        return None
    if not options:
        return None
    return json.dumps(options, ensure_ascii=False)


@router.put("/questions/{quiz_id}", response_model=QuizItemResponse)
async def update_question(
    quiz_id: str,
    req: QuizItemUpdateRequest,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """编辑一道题（题干 / 选项 / 答案 / 解析）

    ## 为什么需要这个端点（B5，2026-10-22）

    改造前**题目只有 GET，没有 PUT/DELETE** —— 卡片有（`/cards/{id}` 的
    PUT 与 DELETE），题目没有。后果是模型出一道坏题时，用户**没有任何入口**
    修它，只能整篇"重新理解"（那会删掉该笔记的全部卡片/题目/复习记录）。

    而 A2/A3′ 的三版提示词重跑之后**题库会整批换掉** ——
    没有审核入口，用户无从对比新旧、也无从留下好的那几道。

    ## 语义：PUT 全量替换（不是 PATCH）

    见 `QuizItemUpdateRequest` 的说明：题目编辑天然是"改完整体提交"，
    用 PUT 就没有"没传 vs 显式清空"的二义性。`explanation=null` 明确表示清空。

    ## 编辑**不**重置复习进度

    这里只改内容，不动 `review_states` 与调度字段 ——
    用户改的可能是解析里的错字，那不该让复习进度归零
    （`models/review_state.py` 开头记的正是这条原则：
    内容可替换、学习历史不该被替换）。
    """
    quiz = await _load_quiz_for_user(db, quiz_id, current_user.id)

    if quiz.question_type == QuestionType.choice and len(req.options or []) < 2:
        # ⚠️ 文案与 `error_messages.py` 的 `QUIZ_OPTIONS_REQUIRED` **逐字一致**
        # （`tests/test_i18n.py::TestTableMatchesRaiseSites` 会比对）。
        # 理由：这个 code 既可能被前端按码翻译、也可能被直接展示 ——
        # 两处文案不同会让用户看到两句不一样的话（而且没人会想到去对）。
        # "少于 2 个无法区分对错"这层解释留在常量注释里，不进用户文案。
        raise AppError(
            QUIZ_OPTIONS_REQUIRED,
            "选择题至少需要 2 个选项",
            400,
        )

    quiz.question = req.question
    quiz.answer = req.answer
    quiz.options = _normalized_options(quiz.question_type, req.options)
    quiz.explanation = req.explanation
    await db.commit()
    await db.refresh(quiz)

    item = QuizItemResponse.model_validate(quiz)
    # 与列表端点同一口径填 note_title（编辑后前端直接用它替换列表里那一行，
    # 不该为了一个标题再拉一次列表）
    if quiz.note_id:
        note_title = (await db.execute(select(Note.title).where(Note.id == quiz.note_id))).scalar_one_or_none()
        item.note_title = note_title or "已删除的笔记"
    else:
        item.note_title = "独立卡片"
    return item


@router.delete("/questions/{quiz_id}", status_code=204)
async def delete_question(
    quiz_id: str,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """删除一道题，并**级联清掉它的复习记录与复习状态**

    ## 级联清单（少清一样就会留下孤儿行）

    | 表 | 判据 | 不清会怎样 |
    |---|---|---|
    | `review_logs` | `quiz_id` | 复习历史指向不存在的题；掌握度统计会把它们算进去 |
    | `review_states` | `item_type='quiz'` **且** `item_id` | **最隐蔽的一条**：那张表用 `(user_id, item_type, item_id)` 定位，`item_id` 是普通字符串（**没有外键**），数据库不会替你级联 —— 孤儿行会一直留在"到期队列"里，用户看到一个点不开的复习项 |
    | `quiz_items` | 自身 | —— |

    ⚠️ `review_states` 里 `item_type='card'` 的行**不能碰**：
    删除题目不该影响那张卡片本身的复习进度（卡片还能整体回忆）。

    ## 为什么不用 `db.delete(quiz)` 让 ORM 自己排顺序

    与 `delete_card` 同一个理由（那段注释记了）：Core 的批量 DELETE
    **立即执行**，而 ORM 的 flush 排序不确定 —— 依赖"先删子行再删父行"
    的隐式顺序在 SQLite 上不保证。显式按子的顺序删，行为才可预期。
    """
    quiz = await _load_quiz_for_user(db, quiz_id, current_user.id)

    await db.execute(sql_delete(ReviewLog).where(ReviewLog.quiz_id == quiz_id))
    await db.execute(
        sql_delete(ReviewState).where(
            ReviewState.user_id == current_user.id,
            ReviewState.item_type == ITEM_TYPE_QUIZ,
            ReviewState.item_id == quiz_id,
        )
    )
    await db.delete(quiz)
    await db.commit()
