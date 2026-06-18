"""
RAG 问答服务模块

本模块提供检索增强生成（RAG）问答功能，基于用户所有笔记的内容回答问题。

主要职责：
- 将用户问题向量化
- 从 Chroma 向量数据库中检索相关文本块
- 拼接上下文
- 调用 LLM 生成回答
- 返回回答 + 引用来源

设计决策：
- 使用已有的 EmbeddingService 和 VectorStore
- 检索时搜索用户所有笔记的相关块（跨笔记检索）
- 返回结果包含引用来源（笔记标题、章节、相关段落）
"""

import asyncio
import logging
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from ..config import get_settings
from ..models.note import Note
from ..models.knowledge_card import KnowledgeCard
from ..services.embedding_service import EmbeddingService, VectorStore
from ..services.llm_service import LLMService

logger = logging.getLogger(__name__)
settings = get_settings()


class RAGService:
    """
    检索增强生成服务

    使用方式：
        service = RAGService()
        result = await service.answer_question("什么是机器学习？", user_id="xxx")
    """

    def __init__(self):
        self._embedding_service = None
        self._vector_store = None
        self._session_factory = None

    def _get_session_factory(self):
        """获取数据库会话工厂"""
        if self._session_factory is None:
            engine = create_async_engine(settings.get_database_url(), echo=False)
            self._session_factory = async_sessionmaker(engine, expire_on_commit=False)
        return self._session_factory

    def _get_embedding_service(self) -> EmbeddingService:
        """延迟初始化嵌入服务"""
        if self._embedding_service is None:
            self._embedding_service = EmbeddingService()
        return self._embedding_service

    def _get_vector_store(self) -> VectorStore:
        """延迟初始化向量存储"""
        if self._vector_store is None:
            self._vector_store = VectorStore()
        return self._vector_store

    async def _search_relevant_chunks(
        self,
        question: str,
        user_id: str,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        从向量数据库中检索与问题相关的文本块

        搜索策略：
        1. 将用户问题向量化
        2. 遍历用户所有笔记的 collection
        3. 对每个 collection 进行相似度搜索
        4. 合并结果，按相似度排序，取 top_k

        Args:
            question: 用户问题
            user_id: 用户 ID
            top_k: 返回最相关的 top_k 个结果

        Returns:
            List[Dict]: 相关文本块列表，每个包含：
                - note_id: 笔记 ID
                - content: 文本内容
                - similarity: 相似度分数
                - block_index: 块索引
        """
        embedding_service = self._get_embedding_service()
        vector_store = self._get_vector_store()

        # 将问题向量化（在独立线程中运行，避免阻塞事件循环）
        # 注意：嵌入模型加载可能消耗大量内存，如果失败则返回空结果
        try:
            question_embedding = await asyncio.to_thread(
                lambda: embedding_service.encode([question])[0]
            )
        except Exception as e:
            logger.warning(f"嵌入模型编码失败，跳过向量检索: {e}")
            return []

        # 获取用户所有笔记
        session_factory = self._get_session_factory()
        async with session_factory() as session:
            result = await session.execute(
                select(Note).where(Note.user_id == user_id)
            )
            notes = result.scalars().all()

        all_results = []

        for note in notes:
            try:
                # 使用 VectorStore 的客户端搜索（在独立线程中运行）
                def _search_note_collection(note_id=note.id, note_title=note.title):
                    try:
                        vector_store._ensure_client()
                        collection_name = vector_store._get_collection_name(note_id)
                        collection = vector_store._client.get_collection(collection_name)
                    except Exception:
                        return []

                    count = collection.count()
                    if count == 0:
                        return []

                    query_results = collection.query(
                        query_embeddings=[question_embedding],
                        n_results=min(3, count),
                        include=["documents", "metadatas", "distances"],
                    )

                    if not query_results["ids"] or not query_results["ids"][0]:
                        return []

                    results = []
                    for i, doc in enumerate(query_results["documents"][0]):
                        distance = query_results["distances"][0][i]
                        similarity = 1.0 / (1.0 + distance)
                        results.append({
                            "note_id": note_id,
                            "note_title": note_title,
                            "content": doc,
                            "similarity": similarity,
                            "block_index": query_results["metadatas"][0][i].get("block_index", 0),
                        })
                    return results

                note_results = await asyncio.to_thread(_search_note_collection)
                all_results.extend(note_results)
            except Exception as e:
                logger.warning(f"搜索笔记 {note.id} 的向量数据失败: {e}")
                continue

        # 按相似度降序排列，取 top_k
        all_results.sort(key=lambda x: x["similarity"], reverse=True)
        return all_results[:top_k]

    async def _search_relevant_cards(
        self,
        question: str,
        user_id: str,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        从知识卡片中检索与问题相关的内容

        作为向量检索的补充，直接从知识卡片表中搜索。

        Args:
            question: 用户问题
            user_id: 用户 ID
            top_k: 返回最相关的 top_k 个结果

        Returns:
            List[Dict]: 相关知识卡片列表
        """
        session_factory = self._get_session_factory()
        async with session_factory() as session:
            # 关键词匹配搜索（取所有卡片）
            result = await session.execute(
                select(KnowledgeCard).where(
                    KnowledgeCard.user_id == user_id,
                ).order_by(KnowledgeCard.created_at.desc())
            )
            cards = result.scalars().all()

        # 简单的关键词匹配评分（支持中文逐字匹配）
        question_lower = question.lower()
        # 提取问题中的关键词（2-4字的中文词组）
        question_keywords = set()
        for length in range(4, 1, -1):  # 4字、3字、2字
            for i in range(len(question_lower) - length + 1):
                question_keywords.add(question_lower[i:i+length])
        # 过滤掉太短或太常见的词
        question_keywords = {kw for kw in question_keywords if len(kw) >= 2}

        scored_cards = []
        for card in cards:
            card_text = f"{card.title} {card.content}".lower()
            score = 0
            for kw in question_keywords:
                if kw in card_text:
                    score += len(kw)  # 更长的匹配给更高分
            if score > 0:
                scored_cards.append({
                    "note_id": card.note_id,
                    "card_id": card.id,
                    "title": card.title,
                    "content": card.content,
                    "chapter_title": card.chapter_title,
                    "score": score,
                })

        scored_cards.sort(key=lambda x: x["score"], reverse=True)
        return scored_cards[:top_k]

    async def answer_question(
        self,
        question: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """
        RAG 问答完整流程

        1. 从向量数据库检索相关文本块
        2. 从知识卡片中检索相关内容
        3. 合并上下文
        4. 调用 LLM 生成回答
        5. 返回回答 + 引用来源

        Args:
            question: 用户问题
            user_id: 用户 ID

        Returns:
            Dict: {"answer": str, "sources": list, "provider": str}
        """
        llm_service = LLMService()

        # 1. 从知识卡片检索（轻量级，不依赖嵌入模型）
        relevant_cards = []
        try:
            relevant_cards = await self._search_relevant_cards(question, user_id)
        except Exception as e:
            logger.warning(f"知识卡片检索失败: {e}")

        # 2. 向量检索暂时禁用（嵌入模型在 API 进程中加载可能导致段错误）
        # TODO: 将嵌入模型加载移到独立服务中
        relevant_chunks = []

        # 3. 合并上下文
        context_parts = []

        if relevant_chunks:
            context_parts.append("=== 相关文档片段 ===")
            for chunk in relevant_chunks:
                context_parts.append(
                    f"[来源: {chunk['note_title']}]\n{chunk['content']}"
                )

        if relevant_cards:
            context_parts.append("\n=== 相关知识卡片 ===")
            for card in relevant_cards:
                chapter_info = f" (章节: {card['chapter_title']})" if card.get("chapter_title") else ""
                context_parts.append(
                    f"[{card['title']}{chapter_info}]\n{card['content']}"
                )

        context = "\n\n".join(context_parts)

        if not context.strip():
            answer = await llm_service.chat(
                [
                    {
                        "role": "system",
                        "content": "你是一个知识渊博的学习助手。用户的问题没有在 TA 的笔记中找到相关信息，"
                                   "请用你自己的知识来回答这个问题。回答时请说明这是基于你的通用知识。",
                    },
                    {"role": "user", "content": question},
                ],
                temperature=0.5,
                max_tokens=2048,
            )
            return {
                "answer": answer,
                "sources": [],
                "provider": llm_service._provider,
            }

        # 4. 调用 LLM 生成回答
        answer = await llm_service.rag_answer(question, context)

        # 5. 构建引用来源
        sources = []
        seen_notes = set()

        for chunk in relevant_chunks:
            if chunk["note_id"] not in seen_notes:
                sources.append({
                    "note_id": chunk["note_id"],
                    "note_title": chunk["note_title"],
                    "chapter_title": None,
                    "relevant_text": chunk["content"][:200],
                })
                seen_notes.add(chunk["note_id"])

        for card in relevant_cards:
            if card["note_id"] not in seen_notes:
                # 查询笔记标题
                session_factory = self._get_session_factory()
                async with session_factory() as session:
                    note_result = await session.execute(
                        select(Note).where(Note.id == card["note_id"])
                    )
                    note = note_result.scalars().first()
                    note_title = note.title if note else "未知笔记"

                sources.append({
                    "note_id": card["note_id"],
                    "note_title": note_title,
                    "chapter_title": card.get("chapter_title"),
                    "relevant_text": card["content"][:200],
                })
                seen_notes.add(card["note_id"])

        return {
            "answer": answer,
            "sources": sources,
            "provider": llm_service._provider,
        }
