"""
AI 理解管道服务模块

本模块提供 Markdown 文档的章节切分、摘要生成和知识点提取功能，
是 AI 理解管道的核心业务逻辑层。

主要职责：
- 将 Markdown 文本按标题层级切分为章节
- 使用多轮对话会话在同一窗口内依次处理每个章节
- 对每个章节合并生成摘要和提取知识点（1次 API 调用完成两项任务）
- 将知识点存入数据库
- 检测知识卡片之间的重复

设计决策：
- 章节切分基于 Markdown 标题（#、##、###、####），不依赖 NLP
- 没有标题的文本归入"未命名章节"
- 所有章节在同一对话窗口内处理，LLM 可参考之前结果避免重复
- 摘要和知识点提取合并为一次 API 调用，减少请求次数
- 知识点提取结果为结构化 JSON，直接存入 KnowledgeCard 表
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..models.knowledge_card import KnowledgeCard, CardType
from ..models.note import Note
from ..services.llm_service import LLMService

logger = logging.getLogger(__name__)


def split_into_chapters(markdown_text: str) -> List[Dict[str, Any]]:
    """
    根据 Markdown 标题层级切分章节

    切分策略：
    - 按 #、##、###、#### 标题切分
    - 没有标题的文本作为"未命名章节"
    - 每个章节保留标题和内容
    - 记录章节在原文中的位置索引
    - 跟踪标题层级路径（hierarchical_path）
    - 合并短章节（内容 < 200 字符）
    - 拆分长章节（内容 > 8000 字符）

    Args:
        markdown_text: Markdown 格式的文本

    Returns:
        List[Dict]: 章节列表，每个包含：
            - chapter_index: 章节索引（从0开始）
            - chapter_title: 章节标题
            - content: 章节内容（不含标题行）
            - level: 标题层级（1-4，0表示未命名章节）
            - hierarchical_path: 标题层级路径（如 "一级 > 二级 > 三级"）
    """
    if not markdown_text or not markdown_text.strip():
        return []

    lines = markdown_text.split("\n")
    chapters: List[Dict[str, Any]] = []
    heading_stack: List[Tuple[int, str]] = []  # [(level, title), ...]
    current_chapter: Optional[Dict[str, Any]] = None
    unnamed_index = 0

    # 匹配 Markdown 标题行（# 到 ####）
    heading_pattern = re.compile(r"^(#{1,4})\s+(.+)$")

    for line in lines:
        match = heading_pattern.match(line)
        if match:
            # 遇到标题，保存上一个章节并开始新章节
            if current_chapter and current_chapter["content"].strip():
                chapters.append(current_chapter)

            level = len(match.group(1))
            title = match.group(2).strip()

            # 更新标题栈：弹出同级或更深层的标题
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
            hierarchical_path = " > ".join(h[1] for h in heading_stack)

            current_chapter = {
                "chapter_index": len(chapters),
                "chapter_title": title,
                "content": "",
                "level": level,
                "hierarchical_path": hierarchical_path,
            }
        else:
            # 非标题行，追加到当前章节
            if current_chapter is None:
                # 标题前的文本，归入未命名章节
                chapter_title = f"未命名章节_{unnamed_index}"
                heading_stack_copy = heading_stack.copy()
                heading_stack_copy.append((0, chapter_title))
                hierarchical_path = " > ".join(h[1] for h in heading_stack_copy)
                current_chapter = {
                    "chapter_index": 0,
                    "chapter_title": chapter_title,
                    "content": "",
                    "level": 0,
                    "hierarchical_path": hierarchical_path,
                }
                unnamed_index += 1
            current_chapter["content"] += line + "\n"

    # 保存最后一个章节
    if current_chapter and current_chapter["content"].strip():
        chapters.append(current_chapter)

    # 重新编号
    for i, chapter in enumerate(chapters):
        chapter["chapter_index"] = i

    # 如果没有切分出任何章节，将整个文本作为一个章节
    if not chapters and markdown_text.strip():
        chapters.append({
            "chapter_index": 0,
            "chapter_title": "全文",
            "content": markdown_text,
            "level": 0,
            "hierarchical_path": "全文",
        })

    # 合并短章节（内容 < 200 字符）
    merged: List[Dict[str, Any]] = []
    for chapter in chapters:
        if merged and len(chapter["content"].strip()) < 200:
            # 合并到前一个章节
            merged[-1]["content"] += "\n\n" + chapter["content"]
        else:
            merged.append(chapter)

    # 合并后重新编号
    for i, chapter in enumerate(merged):
        chapter["chapter_index"] = i
    chapters = merged

    # 拆分长章节（内容 > 8000 字符）
    # F-34:改用"结构感知分段"——按 Markdown 块（标题/段落/列表/表格/代码块/引用）切分，
    # 单个块超限时在安全边界内拆（表格按行、列表按项、段落按句），
    # 未消费尾部自动续接到下一段，避免从句子/表格行/代码块中间截断造成上下文断裂。
    from ..services.markdown_segmenter import make_segments, split_markdown_blocks

    result: List[Dict[str, Any]] = []
    for chapter in chapters:
        if len(chapter["content"]) > 8000:
            blocks = split_markdown_blocks(chapter["content"])
            sub_parts = make_segments(blocks, 8000)
            for idx, part in enumerate(sub_parts):
                suffix = f" (续{idx + 1})" if len(sub_parts) > 1 else ""
                result.append({
                    "chapter_index": len(result),
                    "chapter_title": chapter["chapter_title"] + suffix,
                    "content": part,
                    "level": chapter["level"],
                    "hierarchical_path": chapter["hierarchical_path"] + suffix,
                })
        else:
            chapter["chapter_index"] = len(result)
            result.append(chapter)

    return result


async def generate_chapter_summary(
    chapter: Dict[str, Any],
    llm_service: LLMService,
) -> str:
    """
    生成章节摘要

    Args:
        chapter: 章节字典，包含 chapter_title 和 content
        llm_service: LLM 服务实例

    Returns:
        str: 章节摘要文本
    """
    try:
        summary = await llm_service.summarize_chapter(
            chapter["chapter_title"],
            chapter["content"],
        )
        return summary
    except Exception as e:
        logger.error(
            f"生成章节摘要失败 (chapter={chapter['chapter_title']}): {e}"
        )
        return f"摘要生成失败: {str(e)}"


async def extract_knowledge_points_from_chapter(
    chapter: Dict[str, Any],
    llm_service: LLMService,
) -> List[Dict[str, Any]]:
    """
    从章节中提取知识点

    Args:
        chapter: 章节字典，包含 chapter_title 和 content
        llm_service: LLM 服务实例

    Returns:
        List[Dict]: 知识点列表
    """
    try:
        points = await llm_service.extract_knowledge_points(
            chapter["chapter_title"],
            chapter["content"],
        )
        return points
    except Exception as e:
        logger.error(
            f"提取知识点失败 (chapter={chapter['chapter_title']}): {e}"
        )
        return []


async def save_knowledge_cards(
    db: AsyncSession,
    user_id: str,
    note_id: str,
    chapter: Dict[str, Any],
    summary: str,
    knowledge_points: List[Dict[str, Any]],
) -> List[KnowledgeCard]:
    """
    将知识点存入数据库

    Args:
        db: 数据库会话
        user_id: 用户 ID
        note_id: 笔记 ID
        chapter: 章节信息
        summary: 章节摘要
        knowledge_points: 知识点列表

    Returns:
        List[KnowledgeCard]: 已保存的知识卡片列表
    """
    cards = []
    for point in knowledge_points:
        # 验证 card_type 合法性
        card_type_str = point.get("card_type", "concept")
        try:
            card_type = CardType(card_type_str)
        except ValueError:
            card_type = CardType.concept

        # 提取 source_text，限制长度
        source_text = point.get("source_text", "")
        if len(source_text) > 5000:
            source_text = source_text[:5000]

        card = KnowledgeCard(
            user_id=user_id,
            note_id=note_id,
            card_type=card_type,
            title=point.get("title", "未命名知识点"),
            content=point.get("content", ""),
            summary=summary,
            chapter_title=chapter.get("chapter_title", ""),
            source_text=source_text,
        )
        db.add(card)
        cards.append(card)

    await db.commit()
    # 刷新以获取生成的 id 和时间戳
    for card in cards:
        await db.refresh(card)

    return cards


def _parse_understanding_response(response: str) -> Dict[str, Any]:
    """
    解析理解管道的 JSON 响应

    支持两种格式：
    1. 多章节格式（新版）：{"chapters": [{"chapter_title": "...", "summary": "...", "points": [...]}, ...]}
    2. 单章节格式（兼容旧版）：{"summary": "...", "points": [{...}, ...]}

    容错（JSON 截断修复）：
    - 剥离 LLM 偶尔添加的 markdown 代码块包裹（```json ... ```）
    - 正则提取 JSON 对象
    - 检测"疑似截断"（响应以未闭合的 { 开头且无配对 }），记录明确告警
      （此前 max_tokens=4096 时响应被硬截断，json.loads 失败导致整批章节 0 知识点）

    Args:
        response: LLM 返回的原始文本

    Returns:
        Dict: 格式一 {"chapters": [{"chapter_title": str, "summary": str, "points": list}]}
              格式二 {"summary": str, "points": list}
    """
    if not response or not response.strip():
        logger.warning("理解响应为空")
        return {"summary": "", "points": []}

    # F-33:解析升级为健壮 JSON 解析（围栏剥离 + 尾缀杂文 + 截断抢救），
    # 即使输出被 max_tokens 截断也能抢救出已生成完整的章节/知识点，而非整批丢弃。
    from ..services.llm_service import parse_json_tolerant
    result, info = parse_json_tolerant(response)
    if info.get("status") == "partial":
        # 截断抢救：记录实际抢救出的内容规模，供排查
        recovered = info.get("detail", "")
        logger.warning(
            f"理解响应 JSON 被截断，已完成部分数据抢救: {recovered} | head={response[:120]!r}"
        )
    elif result is None:
        logger.warning(f"理解响应 JSON 解析失败: {response[:200]!r}")
        return {"summary": "", "points": []}

    if not isinstance(result, dict):
        # 兼容 LLM 直接返回数组（知识点列表）
        if isinstance(result, list):
            return {"summary": "", "points": result}
        logger.warning("理解响应 JSON 不是对象或数组")
        return {"summary": "", "points": []}

    # 新版多章节格式
    chapters = result.get("chapters")
    if isinstance(chapters, list) and len(chapters) > 0:
        return {"chapters": chapters}

    # 旧版单章节格式（兼容）
    summary = result.get("summary", "")
    if not isinstance(summary, str):
        summary = str(summary)

    points = []
    for key in ["points", "knowledge_points", "items", "data"]:
        if key in result and isinstance(result[key], list):
            points = result[key]
            break
    else:
        for v in result.values():
            if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                points = v
                break

    return {"summary": summary, "points": points}


async def process_note_understanding(
    db: AsyncSession,
    note_id: str,
    user_id: str,
    markdown_content: str,
) -> Dict[str, Any]:
    """
    处理笔记的完整理解流程（多轮对话 + 批次处理模式）

    使用 ConversationSession 在同一对话窗口内分批处理章节，
    LLM 可以参考之前已提取的知识点，避免重复提取。

    每 3 个章节合并为一次 API 请求，从原来的 N 次降为 ceil(N/3) 次。

    流程：
    1. 切分章节
    2. 创建多轮对话会话
    3. 将章节每 3 个一组，在同一窗口中追问合并的章节内容
    4. 解析返回的多章节 JSON，分别存入数据库

    Args:
        db: 数据库会话
        note_id: 笔记 ID
        user_id: 用户 ID
        markdown_content: 清洗后的 Markdown 内容

    Returns:
        Dict: 处理结果，包含章节摘要和知识点统计
    """
    llm_service = LLMService()

    # 1. 切分章节
    chapters = split_into_chapters(markdown_content)
    logger.info(f"笔记 {note_id} 切分为 {len(chapters)} 个章节")

    # 2. 创建多轮对话会话（所有批次共用同一窗口）
    session = llm_service.create_understanding_session()

    total_cards = 0
    chapter_summaries = []
    batch_size = 3

    # 3. 将章节每 batch_size 个一组，合并发送
    for batch_start in range(0, len(chapters), batch_size):
        batch = chapters[batch_start:batch_start + batch_size]

        # 构建合并的章节内容
        combined_content = ""
        for chapter in batch:
            chapter_title = chapter["chapter_title"]
            chapter_content = chapter["content"]
            max_content = 8000
            if len(chapter_content) > max_content:
                # F-34:防御性截断改为按块边界，不切破段落/表格/代码块。
                # 正常情况下章节已被结构感知分段控制在 ≤8000；此处仅在单一原子块
                # 超限（如一个超长句子）时兜底，剩余部分明确提示已顺延（不静默丢弃）。
                from ..services.markdown_segmenter import truncate_to_complete_blocks
                _prefix, _rest = truncate_to_complete_blocks(chapter_content, max_content)
                if not _prefix and _rest:
                    from ..services.markdown_segmenter import split_oversized_block
                    _chunks = split_oversized_block(chapter_content, max_content)
                    _prefix = _chunks[0] if _chunks else chapter_content[:max_content]
                    _rest = chapter_content[len(_prefix):]
                chapter_content = _prefix + (
                    f"\n...(内容过长，已前置 {len(_prefix)} 字，剩余 {len(_rest)} 字按块顺延)" if _rest else ""
                )
                logger.warning(
                    f"章节 '{chapter_title}' 长度 {len(chapter_content)}>8000，"
                    f"已按块边界截断（剩余 {len(_rest)} 字顺延）"
                )
            combined_content += (
                f"--- 章节 {batch_start // batch_size + 1}.{batch.index(chapter) + 1}: {chapter_title} ---\n"
                f"{chapter_content}\n\n"
            )

        try:
            response = await session.ask(combined_content)
            parsed = _parse_understanding_response(response)

            # JSON 截断/解析失败时重试一次（F-33：重试时提大 max_tokens——
            # 网关按 max_tokens 精确截断（finish_reason=length），提大即可拿全；
            # 重试后仍为空则接受该批为空，避免无限重试）
            if not parsed.get("chapters") and not parsed.get("summary"):
                from ..config import get_settings as _get_settings
                _sf = _get_settings()
                _cur = getattr(session, "_max_tokens", 0) or _sf.llm_json_max_tokens
                escalated = min(max(_cur * 2, _sf.llm_json_max_tokens), _sf.llm_json_max_tokens_ceiling)
                logger.warning(
                    f"笔记 {note_id} 批次 {batch_start // batch_size + 1} "
                    f"解析为空（截断={getattr(session, 'last_truncated', False)}），"
                    f"重试一次并放大 max_tokens={escalated} (对话轮次={session.turn_count})"
                )
                response = await session.ask(combined_content, max_tokens=escalated)
                parsed = _parse_understanding_response(response)

            # 解析多章节或单章节响应
            chapters_data = parsed.get("chapters", [])
            if not chapters_data:
                # 兼容旧版单章节格式
                chapters_data = [{
                    "chapter_title": batch[0]["chapter_title"],
                    "summary": parsed.get("summary", ""),
                    "points": parsed.get("points", []),
                }]

            # 为批次中的每个章节分配结果
            for i, chapter_data in enumerate(chapters_data):
                if i >= len(batch):
                    break
                chapter = batch[i]
                summary = chapter_data.get("summary", "")
                knowledge_points = chapter_data.get("points", [])
                if not isinstance(knowledge_points, list):
                    knowledge_points = []

                logger.info(
                    f"笔记 {note_id} 章节 '{chapter['chapter_title']}' "
                    f"提取完成: 摘要={len(summary)}字, 知识点={len(knowledge_points)}个 "
                    f"(对话轮次={session.turn_count})"
                )

                # 存入数据库
                if knowledge_points:
                    cards = await save_knowledge_cards(
                        db, user_id, note_id, chapter, summary, knowledge_points
                    )
                    total_cards += len(cards)
                else:
                    logger.info(f"章节 '{chapter['chapter_title']}' 未提取到知识点")

                chapter_summaries.append({
                    "chapter_index": chapter["chapter_index"],
                    "chapter_title": chapter["chapter_title"],
                    "summary": summary,
                    "card_count": len(knowledge_points),
                })

        except Exception as e:
            logger.error(
                f"处理批次 {batch_start // batch_size + 1} 失败: {e}",
                exc_info=True,
            )
            for chapter in batch:
                chapter_summaries.append({
                    "chapter_index": chapter["chapter_index"],
                    "chapter_title": chapter["chapter_title"],
                    "summary": f"摘要生成失败: {str(e)}",
                    "card_count": 0,
                })

    logger.info(
        f"笔记 {note_id} 理解完成: "
        f"{len(chapters)} 个章节, {total_cards} 个知识卡片, "
        f"共 {session.turn_count} 轮对话"
    )

    return {
        "note_id": note_id,
        "chapter_count": len(chapters),
        "total_cards": total_cards,
        "chapters": chapter_summaries,
    }


async def detect_card_duplicates(
    db: AsyncSession,
    user_id: str,
    card: KnowledgeCard,
    threshold: int = 5,
) -> List[Dict[str, Any]]:
    """
    检测单张卡片与用户已有卡片之间的重复

    不使用嵌入模型（避免段错误），使用 n-gram 关键词匹配方式，
    与 rag_service.py 的策略一致。

    Args:
        db: 异步数据库会话
        user_id: 用户 ID
        card: 待检测的知识卡片
        threshold: 匹配分数阈值，高于此值视为重复候选

    Returns:
        List[Dict]: 重复候选列表，每个包含 existing_card_id, existing_title, similarity
    """
    result = await db.execute(
        select(KnowledgeCard).where(
            KnowledgeCard.user_id == user_id,
            KnowledgeCard.id != card.id,
        ).limit(500)
    )
    existing_cards = result.scalars().all()
    if not existing_cards:
        return []

    card_text = f"{card.title} {card.content}".lower()
    card_keywords = set()
    for length in range(4, 1, -1):
        for i in range(len(card_text) - length + 1):
            card_keywords.add(card_text[i:i + length])
    card_keywords = {kw for kw in card_keywords if len(kw) >= 2}

    duplicates = []
    for existing in existing_cards:
        existing_text = f"{existing.title} {existing.content}".lower()
        score = 0
        for kw in card_keywords:
            if kw in existing_text:
                score += len(kw)
        if score > threshold:
            duplicates.append({
                "existing_card_id": existing.id,
                "existing_title": existing.title,
                "similarity": min(score / 100.0, 1.0),
                "score": score,
            })

    duplicates.sort(key=lambda x: x["score"], reverse=True)
    return duplicates[:5]
