"""
AI 理解管道异步任务模块

本模块定义了 Celery 异步任务，负责对清洗后的笔记进行 AI 理解处理。
理解管道包括章节切分、摘要生成、知识点提取和题目生成。

主要职责：
- 定义 Celery 任务 understand_document_task
- 执行理解核心逻辑（读取清洗版 → 切分章节 → 生成摘要 → 提取知识点 → 存入知识卡片表）
- 定义 Celery 任务 generate_questions_task
- 执行题目生成逻辑（读取知识卡片 → 调用 LLM 生成题目 → 存入题库表）
- 更新笔记状态（cleaned → learning → archived / learning_failed）

设计决策：
- 理解任务需用户手动触发（不同于清洗的自动触发），因为 LLM 调用消耗 API 额度
- 理解失败不影响清洗结果，仅标记状态为 learning_failed
- 题目生成在理解完成后自动触发
"""

import asyncio
import json
import logging
import time
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from .celery_app import celery_app
from ..config import get_settings
from ..models.note import Note, NoteStatus

settings = get_settings()
logger = logging.getLogger(__name__)

# Celery 任务中需要独立的数据库连接
_understand_engine = None
_understand_session_factory = None


def _get_understand_session():
    """
    获取理解任务专用的数据库会话工厂

    与 clean_tasks.py 中的模式一致，Celery worker 运行在独立进程中，
    需要创建自己的数据库连接。

    Returns:
        async_sessionmaker: 异步会话工厂
    """
    global _understand_engine, _understand_session_factory
    if _understand_session_factory is None:
        _understand_engine = create_async_engine(settings.get_database_url(), echo=False)
        _understand_session_factory = async_sessionmaker(_understand_engine, expire_on_commit=False)
    return _understand_session_factory


async def _update_note_status(note_id: str, status: NoteStatus, error_message: Optional[str] = None, **kwargs):
    """
    更新笔记状态

    Args:
        note_id: 笔记 ID
        status: 新的状态
        error_message: 错误信息（可选）
        **kwargs: 其他需要更新的字段
    """
    session_factory = _get_understand_session()
    async with session_factory() as session:
        result = await session.execute(select(Note).where(Note.id == note_id))
        note = result.scalars().first()
        if not note:
            return
        note.status = status
        if error_message:
            note.error_message = error_message
        for key, value in kwargs.items():
            if hasattr(note, key):
                setattr(note, key, value)
        await session.commit()


async def _understand_document(note_id: str):
    """
    执行文档理解的核心逻辑

    完整流程：
    1. 从数据库获取笔记记录，校验状态
    2. 从对象存储读取清洗后的 Markdown（优先 clean_md，回退 original_md）
    3. 切分章节
    4. 对每个章节调用 LLM 生成摘要
    5. 对每个章节调用 LLM 提取知识点
    6. 知识点存入 knowledge_cards 表
    7. 更新笔记状态为 archived

    Args:
        note_id: 笔记 ID
    """
    start_time = time.monotonic()
    logger.info("文档理解开始: note_id=%s", note_id)

    # 检查笔记是否已被用户删除（状态为 failed 且 error_message 为 "用户手动删除"）
    session_factory = _get_understand_session()
    async with session_factory() as session:
        note = await session.get(Note, note_id)
        if not note or (note.status == NoteStatus.failed and note.error_message == "用户手动删除"):
            logger.info(f"笔记 {note_id} 已被用户删除，跳过理解任务")
            return
    from ..services.storage_service import get_object_bytes, ensure_buckets_exist
    from ..services.understanding_service import process_note_understanding

    ensure_buckets_exist()

    # 1. 获取笔记记录
    session_factory = _get_understand_session()
    async with session_factory() as session:
        result = await session.execute(select(Note).where(Note.id == note_id))
        note = result.scalars().first()
        if not note:
            return
        user_id = note.user_id
        clean_md_path = note.clean_md_path
        original_md_path = note.original_md_path

    # 2. 读取 Markdown 内容（优先清洗版）
    md_path = clean_md_path or original_md_path
    if not md_path:
        await _update_note_status(
            note_id, NoteStatus.learning_failed,
            error_message="笔记缺少 Markdown 文件路径",
        )
        return

    try:
        md_bytes = get_object_bytes(settings.minio_bucket_markdown, md_path)
        markdown_content = md_bytes.decode("utf-8")
    except Exception as e:
        await _update_note_status(
            note_id, NoteStatus.learning_failed,
            error_message=f"读取 Markdown 文件失败: {str(e)}",
        )
        return

    if not markdown_content.strip():
        await _update_note_status(
            note_id, NoteStatus.learning_failed,
            error_message="Markdown 内容为空",
        )
        return

    # 3-6. 执行理解流程（切分章节 → 生成摘要 → 提取知识点 → 存入数据库）
    async with session_factory() as session:
        result = await process_note_understanding(
            db=session,
            note_id=note_id,
            user_id=user_id,
            markdown_content=markdown_content,
        )

    logger.info(
        f"笔记 {note_id} 理解完成: "
        f"{result['chapter_count']} 个章节, "
        f"{result['total_cards']} 个知识卡片"
    )

    # 7. 更新笔记状态为 archived
    await _update_note_status(note_id, NoteStatus.archived)

    elapsed = time.monotonic() - start_time
    logger.info(
        "文档理解完成: note_id=%s, card_count=%d, elapsed=%.1fs",
        note_id, result.get("total_cards", 0), elapsed,
    )

    # 8. 自动触发题目生成
    try:
        generate_questions_task.delay(note_id)
    except Exception as e:
        logger.warning(f"触发题目生成任务失败 (note_id={note_id}): {e}")


def _questions_for_card(card: dict, target_categories: Optional[list], target_difficulty: Optional[str]) -> list:
    """
    根据卡片类别与目标类别，决定该卡片应生成的题目数量与难度

    - 盲点卡片（blind_spot）在目标类别中：2 题，1 中 + 1 难
    - 拓展卡片（extension）在目标类别中：1 题，难
    - 其他情况：1 题，难度由 LLM 判定（None）

    Args:
        card: 知识卡片字典，需含 card_category 字段
        target_categories: 目标卡片类别列表，如 ["blind_spot"]；为 None 或空时按默认行为
        target_difficulty: 目标难度倾向（暂不强制使用，仅作为提示）

    Returns:
        list: 题目需求列表，每项形如 {"count": int, "difficulty": Optional[str]}
    """
    cat = card.get("card_category")
    if target_categories and cat in target_categories:
        if cat == "blind_spot":
            # 盲点：2 题，1 中 + 1 难
            return [
                {"count": 1, "difficulty": "medium"},
                {"count": 1, "difficulty": "hard"},
            ]
        elif cat == "extension":
            # 拓展：1 题，难
            return [{"count": 1, "difficulty": "hard"}]
    # 默认（非目标或无 target）：1 题，难度由 LLM 判定
    return [{"count": 1, "difficulty": None}]


async def _generate_questions(note_id: str, target_categories: Optional[list] = None, target_difficulty: Optional[str] = None):
    """
    执行题目生成的核心逻辑（多轮对话模式）

    使用 ConversationSession 在同一对话窗口内依次处理每个批次，
    LLM 可以参考之前已生成的题目，避免重复或雷同。

    完整流程：
    1. 从数据库获取笔记关联的所有知识卡片
    2. 创建多轮对话会话
    3. 将卡片分批（每批30个），在同一窗口中依次追问
    4. 题目存入 quiz_items 表

    当 target_categories 非空时，启用定向出题：盲点卡生成 1 中 + 1 难，
    拓展卡生成 1 难；并在批次 prompt 中标注每卡期望题数与难度。
    target_categories 为 None 时，行为与原系统完全一致（每卡 1 题）。

    Args:
        note_id: 笔记 ID
        target_categories: 目标卡片类别列表，如 ["blind_spot"]、["extension"]；默认 None
        target_difficulty: 目标难度倾向（暂不强制使用，仅作为提示）；默认 None
    """
    from ..models.knowledge_card import KnowledgeCard
    from ..models.quiz_item import QuizItem, QuestionType, DifficultyLevel
    from ..services.llm_service import LLMService

    # 检查笔记是否已被用户删除
    session_factory = _get_understand_session()
    async with session_factory() as session:
        note = await session.get(Note, note_id)
        if not note or (note.status == NoteStatus.failed and note.error_message == "用户手动删除"):
            logger.info(f"笔记 {note_id} 已被用户删除，跳过题目生成任务")
            return

    llm_service = LLMService()

    # 1. 获取知识卡片（立即提取属性值，避免 commit 后延迟加载问题）
    async with session_factory() as session:
        result = await session.execute(
            select(KnowledgeCard).where(KnowledgeCard.note_id == note_id)
        )
        cards = result.scalars().all()

        if not cards:
            logger.info(f"笔记 {note_id} 没有知识卡片，跳过题目生成")
            return

        # 立即提取所有属性值到字典列表，避免 commit 后延迟加载
        cards_data = [
            {
                "id": card.id,
                "user_id": card.user_id,
                "title": card.title,
                "content": card.content,
                "card_type": card.card_type.value,
                "card_category": card.card_category.value,
            }
            for card in cards
        ]

    # 2. 创建多轮对话会话（所有批次共用同一窗口）
    question_session = llm_service.create_question_session()

    # 3. 分批生成题目（在同一对话窗口中依次追问）
    total_questions = 0
    batch_size = 30  # 每批30个卡片，减少 API 调用次数

    for batch_start in range(0, len(cards_data), batch_size):
        batch = cards_data[batch_start:batch_start + batch_size]
        try:
            # 构建本批知识点文本
            cards_text = ""
            for i, card in enumerate(batch):
                cards_text += f"\n--- 知识点 {i+1} ---\n"
                cards_text += f"标题：{card['title']}\n"
                cards_text += f"类型：{card['card_type']}\n"
                if target_categories:
                    # 定向出题模式：追加类别与题数/难度提示
                    cards_text += f"类别：{card['card_category']}\n"
                cards_text += f"内容：{card['content'][:500]}\n"
                if target_categories:
                    reqs = _questions_for_card(card, target_categories, target_difficulty)
                    total_q = sum(r["count"] for r in reqs)
                    diff_hint = ",".join(r["difficulty"] for r in reqs if r["difficulty"]) or "自动"
                    cards_text += f"[本知识点需生成 {total_q} 道题，难度倾向：{diff_hint}]\n"

            # 在同一对话窗口中追问
            response = await question_session.ask(cards_text)

            # 解析 JSON 响应
            questions = _parse_questions_response(response)

            # 存入数据库（使用新会话）
            async with session_factory() as session:
                for q in questions:
                    # 获取 card_index 对应的卡片
                    card_index = q.get("card_index", 1)
                    if isinstance(card_index, int) and 1 <= card_index <= len(batch):
                        card = batch[card_index - 1]
                    else:
                        card = batch[0]

                    # 验证 question_type
                    q_type_str = q.get("question_type", "choice")
                    try:
                        question_type = QuestionType(q_type_str)
                    except ValueError:
                        question_type = QuestionType.choice

                    # 验证 difficulty（优先使用 LLM 返回值，若为空则用定向出题建议）
                    diff_str = q.get("difficulty")
                    if not diff_str:
                        if target_categories:
                            reqs = _questions_for_card(card, target_categories, target_difficulty)
                            diff_str = reqs[0]["difficulty"] if reqs and reqs[0]["difficulty"] else "medium"
                        else:
                            diff_str = "medium"
                    try:
                        difficulty = DifficultyLevel(diff_str)
                    except ValueError:
                        difficulty = DifficultyLevel.medium

                    quiz_item = QuizItem(
                        user_id=card["user_id"],
                        card_id=card["id"],
                        note_id=note_id,
                        question_type=question_type,
                        difficulty=difficulty,
                        question=q.get("question", ""),
                        answer=q.get("answer", ""),
                        options=json.dumps(q.get("options"), ensure_ascii=False) if q.get("options") else None,
                        explanation=q.get("explanation"),
                    )
                    session.add(quiz_item)
                    total_questions += 1

                await session.commit()
                logger.info(
                    f"笔记 {note_id} 批次 {batch_start//batch_size + 1} "
                    f"题目生成: {len(questions)} 道 (累计 {total_questions}) "
                    f"(对话轮次={question_session.turn_count})"
                )

        except Exception as e:
            logger.error(
                f"为笔记 {note_id} 批次 {batch_start//batch_size + 1} 生成题目失败: {e}",
                exc_info=True,
            )
            continue

    logger.info(
        f"笔记 {note_id} 题目生成完成: {total_questions} 道题, "
        f"共 {question_session.turn_count} 轮对话"
    )


def _parse_questions_response(response: str) -> list:
    """
    解析题目生成的 JSON 响应

    预期格式：{"questions": [{card_index, question_type, ...}, ...]}
    包含容错处理，应对 LLM 返回格式不规范的情况。

    Args:
        response: LLM 返回的原始文本

    Returns:
        list: 题目列表
    """
    import re as _re

    try:
        result = json.loads(response)
    except json.JSONDecodeError:
        json_match = _re.search(r'\{[\s\S]*\}', response)
        if json_match:
            try:
                result = json.loads(json_match.group())
            except json.JSONDecodeError:
                logger.warning(f"题目生成响应 JSON 解析失败: {response[:200]}")
                return []
        else:
            logger.warning(f"题目生成响应中未找到 JSON: {response[:200]}")
            return []

    if isinstance(result, dict):
        for key in ["questions", "items", "data"]:
            if key in result and isinstance(result[key], list):
                items = result[key]
                if len(items) > 0 and isinstance(items[0], dict):
                    return items
                return items
        # 尝试找到第一个列表值
        for v in result.values():
            if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                return v
        logger.warning(f"未找到有效的题目列表，响应键: {list(result.keys())}")
        return []
    if isinstance(result, list):
        if len(result) > 0 and isinstance(result[0], dict):
            return result
        return []
    return []


@celery_app.task(bind=True, max_retries=2, default_retry_delay=60, rate_limit="3/m")
def understand_document_task(self, note_id: str):
    """
    Celery 任务：文档理解

    作为 Celery 异步任务执行，由用户手动触发。
    使用 asyncio.run() 在同步的 Celery 任务中运行异步的理解逻辑。

    Args:
        self: Celery 任务实例
        note_id: 笔记 ID
    """
    try:
        asyncio.run(_update_note_status(note_id, NoteStatus.learning))
        asyncio.run(_understand_document(note_id))
    except Exception as exc:
        logger.error(f"理解任务异常 (note_id={note_id}): {exc}", exc_info=True)
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            try:
                asyncio.run(_update_note_status(
                    note_id, NoteStatus.learning_failed,
                    error_message=f"理解任务重试失败: {str(exc)}",
                ))
            except Exception as update_err:
                logger.error(f"更新笔记状态失败 (note_id={note_id}): {update_err}")


@celery_app.task(bind=True, max_retries=2, default_retry_delay=60)
def generate_questions_task(self, note_id: str, target_categories: Optional[list] = None, target_difficulty: Optional[str] = None):
    """
    Celery 任务：题目生成

    由理解任务完成后自动触发，也可由用户手动触发。

    Args:
        self: Celery 任务实例
        note_id: 笔记 ID
        target_categories: 目标卡片类别列表，如 ["blind_spot"]、["extension"]；默认 None（兼容原行为）
        target_difficulty: 目标难度倾向（暂不强制使用，仅作为提示）；默认 None
    """
    try:
        asyncio.run(_generate_questions(note_id, target_categories, target_difficulty))
    except Exception as exc:
        logger.error(f"题目生成任务异常 (note_id={note_id}): {exc}", exc_info=True)
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            logger.error(f"题目生成任务重试失败 (note_id={note_id})")
