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

import json
import logging
from typing import Optional, List

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, delete as sql_delete, or_, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.app_error import (
    CARD_NOT_FOUND,
    EMPTY_QUESTION,
    NOTE_NOT_FOUND,
    NOTE_STATUS_INVALID,
    UNDERSTANDING_IN_PROGRESS,
    UNDERSTANDING_NO_CARDS,
    AppError,
)
from ..database import get_db
from ..models.note import Note, NoteStatus
from ..models.user import User
from ..models.knowledge_card import KnowledgeCard
from ..models.quiz_item import QuizItem
from ..models.review_log import ReviewLog
from ..models.card_relation import CardRelation
from ..api.auth import get_current_user_dependency
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
    QuestionRequest,
    QuestionAnswerResponse,
    AnswerSource,
    GenerateQuestionsResponse,
)
from ..services.note_service import get_note_detail
from ..services.understanding_service import detect_card_duplicates
from ..services.rag_service import RAGService
from ..services.llm_service import LLMService
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
        )

    # archived 笔记未确认时，只返回影响数量，不执行任何删除（见 docs/decisions.md#F-02）
    if note.status == NoteStatus.archived and not req.confirm:
        impact = await _count_understanding_impact(db, note_id)
        return UnderstandingStartResponse(
            id=note_id,
            status=note.status,
            message="重新理解将删除该笔记现有的全部学习成果（卡片、题目、复习记录、图谱关系），确认后不可恢复",
            requires_confirm=True,
            impact=impact,
        )

    # 重新学习前清空旧产物（无论当前状态），避免重复卡片
    # 批量删除关联的复习记录
    quiz_ids_result = await db.execute(
        select(QuizItem.id).where(QuizItem.note_id == note_id)
    )
    quiz_ids = [row[0] for row in quiz_ids_result.all()]
    if quiz_ids:
        await db.execute(sql_delete(ReviewLog).where(ReviewLog.quiz_id.in_(quiz_ids)))
    # 批量删除关联的卡片关系
    card_ids_result = await db.execute(
        select(KnowledgeCard.id).where(KnowledgeCard.note_id == note_id)
    )
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
    understand_document_task.delay(note_id)

    return UnderstandingStartResponse(
        id=note_id,
        status=NoteStatus.learning,
        message="理解任务已触发",
    )


async def _count_understanding_impact(
    db: AsyncSession, note_id: str
) -> UnderstandingImpact:
    """统计重新理解将删除的旧产物数量（见 docs/decisions.md#F-02）"""
    from sqlalchemy import func as sa_func

    cards = (await db.execute(
        select(sa_func.count()).select_from(KnowledgeCard).where(
            KnowledgeCard.note_id == note_id
        )
    )).scalar() or 0

    quizzes = (await db.execute(
        select(sa_func.count()).select_from(QuizItem).where(
            QuizItem.note_id == note_id
        )
    )).scalar() or 0

    review_logs = (await db.execute(
        select(sa_func.count()).select_from(ReviewLog).where(
            ReviewLog.note_id == note_id
        )
    )).scalar() or 0

    relations = 0
    card_ids_result = await db.execute(
        select(KnowledgeCard.id).where(KnowledgeCard.note_id == note_id)
    )
    card_ids = [row[0] for row in card_ids_result.all()]
    if card_ids:
        relations = (await db.execute(
            select(sa_func.count()).select_from(CardRelation).where(
                or_(
                    CardRelation.card_id_1.in_(card_ids),
                    CardRelation.card_id_2.in_(card_ids),
                )
            )
        )).scalar() or 0

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
        raise AppError(NOTE_NOT_FOUND, "笔记不存在", 404)

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
        note_result = await db.execute(
            select(Note.title).where(Note.id == card.note_id)
        )
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
        note_result = await db.execute(
            select(Note.title).where(Note.id == card.note_id)
        )
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
        sql_update(KnowledgeCard)
        .where(KnowledgeCard.parent_card_id == card_id)
        .values(parent_card_id=None)
    )

    # 删除关联的复习记录
    quiz_ids_result = await db.execute(
        select(QuizItem.id).where(QuizItem.card_id == card_id)
    )
    quiz_ids = [row[0] for row in quiz_ids_result.all()]
    if quiz_ids:
        await db.execute(
            sql_delete(ReviewLog).where(ReviewLog.quiz_id.in_(quiz_ids))
        )

    # 删除关联的题目（Core 批量删除立即执行，确保先于卡片本身的 DELETE，
    # 避免 ORM flush 排序不确定导致 quiz_items 外键约束失败）
    await db.execute(
        sql_delete(QuizItem).where(QuizItem.card_id == card_id)
    )
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
):
    """
    RAG 问答流式接口（SSE）

    通过 Server-Sent Events 逐 token 返回 LLM 生成的回答，
    适合前端实时展示生成过程。

    流程：
    1. 调用 rag_service.retrieve_context() 完成检索阶段（不调用 LLM）
    2. 首事件下发 retrieval_status（meta 事件，供前端展示降级提示）
    3. 复用与 rag_answer() 一致的 system prompt 构建 messages（命中 DeepSeek 提示词缓存）
    4. 调用 llm_service.chat_stream() 流式生成回答
    5. 流式结束后发送 sources 与 done 事件

    SSE 事件格式：
    - event: meta    data: {"retrieval_status": "...", "provider": "..."}  首事件，检索降级状态
    - event: token   data: {"content": "..."}                每个 token 片段
    - event: sources data: {"sources": [...], "provider": "..."}  流式结束后返回引用来源
    - event: done    data: {}                                结束标记
    - event: error   data: {"message": "..."}                异常情况
    """

    async def event_stream():
        try:
            # 拒绝空/纯空白问题，避免无意义地消耗一次 LLM 调用（见 docs/decisions.md#F-31）
            if not req.question or not req.question.strip():
                yield f"event: error\ndata: {json.dumps({'message': '问题不能为空', 'error_code': 'EMPTY_QUESTION'}, ensure_ascii=False)}\n\n"
                return

            # 1. 检索阶段（不调用 LLM）
            rag_service = RAGService()
            retrieval = await rag_service.retrieve_context(
                question=req.question,
                user_id=current_user.id,
            )
            context = retrieval["context"]
            sources = retrieval["sources"]
            provider = retrieval["provider"]
            retrieval_status = retrieval.get("retrieval_status", "")

            # 2. 首事件下发检索降级状态（sources 前，供前端即时提示）
            yield f"event: meta\ndata: {json.dumps({'retrieval_status': retrieval_status, 'provider': provider}, ensure_ascii=False)}\n\n"

            # 3. 构建 messages（system prompt 与 LLMService.rag_answer 保持一致以命中缓存）
            llm_service = LLMService()
            if not context.strip():
                # 无检索结果，使用 LLM 自身知识回答
                messages = [
                    {
                        "role": "system",
                        "content": "你是一个知识渊博的学习助手。用户的问题没有在 TA 的笔记中找到相关信息，"
                                   "请用你自己的知识来回答这个问题。回答时请说明这是基于你的通用知识。",
                    },
                    {"role": "user", "content": req.question},
                ]
            else:
                # 基于检索上下文回答（与 rag_answer 一致的 prompt）
                messages = [
                    {
                        "role": "system",
                        "content": (
                            "你是一个知识渊博的学习助手。请根据提供的参考资料回答用户问题。\n\n"
                            "回答原则：\n"
                            "1. 优先使用参考资料：如果参考资料中包含相关信息，请以其为主要依据\n"
                            "2. 自主知识补充：如果参考资料不足或没有相关信息，可以结合你自己的知识来回答，"
                            "但请说明这部分是基于你的知识补充的\n"
                            "3. 诚实标注：如果回答中既有参考资料的内容，也有你自己的知识，请尽量区分\n"
                            "4. 回答应详细有用：不要简单地回复没有相关信息，而是尽力提供有价值的回答\n"
                            "5. 适当引用：回答中可引用参考资料中的原文来增强可信度"
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"参考资料：\n{context}\n\n问题：{req.question}",
                    },
                ]

            # 4. 流式输出 token
            async for chunk in llm_service.chat_stream(messages, scene="rag_answer_stream"):
                yield f"event: token\ndata: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"

            # 5. 发送 sources 事件
            yield f"event: sources\ndata: {json.dumps({'sources': sources, 'provider': provider}, ensure_ascii=False)}\n\n"

            # 6. 发送 done 事件
            yield "event: done\ndata: {}\n\n"
        except Exception as e:
            logger.warning(f"SSE 流式问答失败: {e}", exc_info=True)
            yield f"event: error\ndata: {json.dumps({'message': str(e)}, ensure_ascii=False)}\n\n"

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
):
    """触发题目生成"""
    note = await get_note_detail(db, note_id, current_user.id)
    if not note:
        raise AppError(NOTE_NOT_FOUND, "笔记不存在", 404)

    # 检查是否有知识卡片
    count_result = await db.execute(
        select(func.count()).select_from(KnowledgeCard).where(
            KnowledgeCard.note_id == note_id,
            KnowledgeCard.user_id == current_user.id,
        )
    )
    card_count = count_result.scalar() or 0

    if card_count == 0:
        raise AppError(UNDERSTANDING_NO_CARDS, "该笔记暂无知识卡片，请先触发理解管道", 400)

    # 触发 Celery 题目生成任务（透传定向出题参数）
    generate_questions_task.delay(note_id, target_categories, target_difficulty)

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
        raise AppError(NOTE_NOT_FOUND, "笔记不存在", 404)

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
    keyword: Optional[str] = Query(None, description="搜索关键词，匹配题目内容"),
):
    """获取当前用户所有题目（分页），支持关键词搜索"""
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
