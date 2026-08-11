"""
RAG 问答服务模块

本模块提供检索增强生成（RAG）问答功能，基于用户所有笔记的内容回答问题。

主要职责：
- 通过 Celery worker 调用嵌入模型将问题向量化（隔离模型加载，避免主进程段错误）
- 从 Chroma 向量数据库中检索相关文本块（通过 Celery 任务）
- 使用 BM25 算法从知识卡片中检索相关内容（纯 Python 实现）
- 使用 n-gram 关键词匹配检索知识卡片
- 使用 RRF（Reciprocal Rank Fusion）融合三种检索结果
- 拼接上下文，调用 LLM 生成回答
- 返回回答 + 引用来源

设计决策：
- 嵌入模型加载隔离到 Celery worker 进程，避免在 FastAPI 主进程中
  加载 BGE-M3 导致段错误（0xC0000005）
- 混合检索策略：向量检索（语义）+ BM25（关键词）+ n-gram（字符匹配）
- RRF 融合三种检索结果，互补提升召回率
- 嵌入任务超时或失败时，自动降级为 BM25 + n-gram 检索
- 返回结果包含引用来源（笔记标题、章节、相关段落）
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from ..config import get_settings
from ..models.note import Note
from ..models.knowledge_card import KnowledgeCard
from ..services.llm_service import LLMService

logger = logging.getLogger(__name__)
settings = get_settings()


class RAGService:
    """
    检索增强生成服务

    使用混合检索策略（向量 + BM25 + n-gram）从用户笔记中召回相关内容，
    通过 RRF 融合后作为上下文调用 LLM 生成回答。

    使用方式：
        service = RAGService()
        result = await service.answer_question("什么是机器学习？", user_id="xxx")
    """

    def __init__(self):
        self._session_factory = None

    def _get_session_factory(self):
        """获取数据库会话工厂"""
        if self._session_factory is None:
            engine = create_async_engine(settings.get_database_url(), echo=False)
            self._session_factory = async_sessionmaker(engine, expire_on_commit=False)
        return self._session_factory

    async def _encode_via_celery(self, text: str) -> Optional[List[float]]:
        """
        通过 Celery worker 编码文本，返回嵌入向量

        将嵌入模型加载隔离到 Celery worker 进程中，避免在 FastAPI 主进程中
        加载 BGE-M3 模型导致段错误。任务超时或失败时返回 None，调用方应降级处理。

        Args:
            text: 待编码的文本

        Returns:
            Optional[List[float]]: 嵌入向量，失败时返回 None
        """
        try:
            from ..tasks.celery_app import celery_app
            task = celery_app.send_task(
                "app.tasks.embedding_tasks.encode_text",
                args=[[text]],
            )
            result = task.get(timeout=10)
            if result:
                return result[0]
            return None
        except Exception as e:
            logger.warning(f"Celery 嵌入编码失败，将降级为 BM25 + n-gram 检索: {e}")
            return None

    async def _search_vectors_via_celery(
        self,
        question_embedding: List[float],
        user_id: str,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        通过 Celery worker 执行向量搜索

        Args:
            question_embedding: 问题的嵌入向量
            user_id: 用户 ID
            top_k: 返回最相关的 top_k 个结果

        Returns:
            List[Dict]: 相关文本块列表，失败时返回空列表
        """
        try:
            from ..tasks.celery_app import celery_app
            task = celery_app.send_task(
                "app.tasks.embedding_tasks.search_vectors",
                args=[user_id, question_embedding, top_k],
            )
            result = task.get(timeout=15)
            return result if result else []
        except Exception as e:
            logger.warning(f"Celery 向量搜索失败: {e}")
            return []

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """
        分词：按空白和标点分割，中文字符作为 2-gram

        用于 BM25 检索的分词器，兼顾中英文：
        - 中文：提取连续中文字符的 2-gram（bigram），平衡召回率和精度
        - 英文/数字：按空白和标点分割，小写化

        Args:
            text: 待分词的文本

        Returns:
            List[str]: token 列表
        """
        if not text:
            return []
        tokens: List[str] = []
        # 提取中文字符的 2-gram
        chinese_chars = re.findall(r"[\u4e00-\u9fff]", text)
        for i in range(len(chinese_chars) - 1):
            tokens.append(chinese_chars[i] + chinese_chars[i + 1])
        # 分割非中文部分（英文/数字），小写化
        non_chinese = re.sub(r"[\u4e00-\u9fff]", " ", text)
        words = re.findall(r"[a-zA-Z0-9]+", non_chinese.lower())
        tokens.extend(words)
        return tokens

    async def _search_bm25(
        self,
        question: str,
        user_id: str,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        BM25 关键词检索（纯 Python 实现，无外部依赖）

        从用户的知识卡片中检索与问题相关的内容，使用 Okapi BM25 算法计算相关性。

        BM25 公式：
            score(D, Q) = sum_t IDF(t) * (f(t, D) * (k1 + 1)) /
                          (f(t, D) + k1 * (1 - b + b * |D| / avgdl))
            IDF(t) = log((N - df(t) + 0.5) / (df(t) + 0.5) + 1)

        Args:
            question: 用户问题
            user_id: 用户 ID
            top_k: 返回最相关的 top_k 个结果

        Returns:
            List[Dict]: 相关内容列表，每个包含：
                - note_id: 笔记 ID
                - note_title: None（后续在 sources 构建时回填）
                - content: 卡片内容
                - similarity: BM25 分数
                - block_index: 0
        """
        session_factory = self._get_session_factory()
        async with session_factory() as session:
            result = await session.execute(
                select(KnowledgeCard).where(KnowledgeCard.user_id == user_id)
            )
            cards = result.scalars().all()

        if not cards:
            return []

        query_tokens = self._tokenize(question)
        if not query_tokens:
            return []

        # 构建文档列表
        docs: List[Dict[str, Any]] = []
        for card in cards:
            doc_text = f"{card.title} {card.content}"
            doc_tokens = self._tokenize(doc_text)
            docs.append({
                "card": card,
                "tokens": doc_tokens,
                "len": len(doc_tokens),
            })

        if not docs:
            return []

        # BM25 参数
        k1 = 1.5
        b = 0.75
        N = len(docs)
        avgdl = sum(d["len"] for d in docs) / N if N > 0 else 0.0

        # 计算每个 token 的文档频率 df 和 IDF
        df: Dict[str, int] = {}
        for doc in docs:
            unique_tokens = set(doc["tokens"])
            for token in unique_tokens:
                df[token] = df.get(token, 0) + 1

        idf: Dict[str, float] = {}
        for token, freq in df.items():
            idf[token] = math.log((N - freq + 0.5) / (freq + 0.5) + 1)

        # 计算每个文档的 BM25 分数
        scored: List[Dict[str, Any]] = []
        for doc in docs:
            score = 0.0
            token_freq: Dict[str, int] = {}
            for token in doc["tokens"]:
                token_freq[token] = token_freq.get(token, 0) + 1

            for query_token in query_tokens:
                if query_token not in token_freq:
                    continue
                tf = token_freq[query_token]
                idf_val = idf.get(query_token, 0.0)
                numerator = tf * (k1 + 1)
                if avgdl > 0:
                    denominator = tf + k1 * (1 - b + b * doc["len"] / avgdl)
                else:
                    denominator = tf + k1
                if denominator > 0:
                    score += idf_val * numerator / denominator

            if score > 0:
                card = doc["card"]
                scored.append({
                    "note_id": card.note_id,
                    "note_title": None,
                    "content": card.content,
                    "similarity": score,
                    "block_index": 0,
                    # 保留卡片特有字段，便于后续构建 sources
                    "card_id": card.id,
                    "title": card.title,
                    "chapter_title": card.chapter_title,
                })

        scored.sort(key=lambda x: x["similarity"], reverse=True)
        return scored[:top_k]

    @staticmethod
    def _rrf_fusion(
        vector_results: List[Dict[str, Any]],
        bm25_results: List[Dict[str, Any]],
        ngram_results: List[Dict[str, Any]],
        k: int = 60,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Reciprocal Rank Fusion（RRF）融合多路检索结果

        RRF 公式：score(d) = sum_i 1 / (k + rank_i(d))
        其中 rank_i(d) 是文档 d 在第 i 路结果列表中的排名（从 1 开始），
        k 是平滑常数（默认 60），平衡头部和尾部结果的权重。

        融合策略：
        - 以 (note_id, content 前缀) 为键去重
        - 累加各路 RRF 分数
        - 按融合分数降序排列，取 top_k

        Args:
            vector_results: 向量检索结果列表
            bm25_results: BM25 检索结果列表
            ngram_results: n-gram 检索结果列表
            k: RRF 平滑常数，默认 60
            top_k: 返回的最终结果数，默认 5

        Returns:
            List[Dict]: 融合后的结果列表，每个包含：
                - note_id, note_title, content, similarity, block_index
                - 可能包含 card_id, title, chapter_title（来自卡片检索）
        """
        fused: Dict[tuple, Dict[str, Any]] = {}

        for result_list in [vector_results, bm25_results, ngram_results]:
            for rank_idx, item in enumerate(result_list):
                note_id = item.get("note_id")
                content = item.get("content", "") or ""
                # 以 (note_id, content 前 200 字符) 为去重键
                dedupe_key = (note_id, content[:200])

                # rank 从 1 开始
                rrf_score = 1.0 / (k + rank_idx + 1)

                if dedupe_key not in fused:
                    merged_item = {
                        "note_id": note_id,
                        "note_title": item.get("note_title"),
                        "content": content,
                        "similarity": 0.0,
                        "block_index": item.get("block_index", 0),
                    }
                    # 保留卡片特有字段（来自 BM25 / n-gram 结果）
                    for extra_key in ("card_id", "title", "chapter_title"):
                        if extra_key in item:
                            merged_item[extra_key] = item[extra_key]
                    fused[dedupe_key] = merged_item
                fused[dedupe_key]["similarity"] += rrf_score

        sorted_results = sorted(
            fused.values(), key=lambda x: x["similarity"], reverse=True
        )
        return sorted_results[:top_k]

    async def _search_relevant_cards(
        self,
        question: str,
        user_id: str,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        从知识卡片中检索与问题相关的内容（n-gram 字符匹配）

        作为向量检索和 BM25 的补充，使用字符级 n-gram 匹配，
        对中文短查询有较好的召回效果。

        Args:
            question: 用户问题
            user_id: 用户 ID
            top_k: 返回最相关的 top_k 个结果

        Returns:
            List[Dict]: 相关知识卡片列表，每个包含：
                - note_id, card_id, title, content, chapter_title, score
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

    async def retrieve_context(
        self,
        question: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """
        仅执行检索阶段，返回上下文和引用来源（不调用 LLM）

        完整流程：
        1. 通过 Celery worker 编码问题（隔离嵌入模型加载）
        2. 通过 Celery worker 执行向量检索
        3. 执行 BM25 检索（纯 Python）
        4. 执行 n-gram 检索（复用 _search_relevant_cards）
        5. 使用 RRF 融合三路结果，取 top 5 作为上下文
        6. 拼接上下文字符串
        7. 构建引用来源（回查笔记标题）

        降级策略：
        - 向量编码/检索失败：仅使用 BM25 + n-gram
        - BM25 失败：仅使用 n-gram
        - 全部失败：返回空上下文与空来源

        Args:
            question: 用户问题
            user_id: 用户 ID

        Returns:
            Dict: {"context": str, "sources": list, "provider": str}
                - context: 拼接好的上下文字符串（可能为空）
                - sources: 引用来源列表，每个含 note_id/note_title/chapter_title/relevant_text
                - provider: LLM 提供商标识（deepseek/glm）
        """
        provider = settings.get_llm_config()["provider"]

        # 1. 通过 Celery 编码问题
        question_embedding = await self._encode_via_celery(question)

        # 2. 向量检索（仅当编码成功时）
        vector_results: List[Dict[str, Any]] = []
        if question_embedding is not None:
            try:
                vector_results = await self._search_vectors_via_celery(
                    question_embedding, user_id, top_k=5
                )
            except Exception as e:
                logger.warning(f"向量检索失败，降级为 BM25 + n-gram: {e}")
                vector_results = []
        else:
            logger.warning("嵌入编码失败，跳过向量检索，使用 BM25 + n-gram")

        # 3. BM25 检索
        bm25_results: List[Dict[str, Any]] = []
        try:
            bm25_results = await self._search_bm25(question, user_id, top_k=5)
        except Exception as e:
            logger.warning(f"BM25 检索失败: {e}")

        # 4. n-gram 检索
        ngram_results: List[Dict[str, Any]] = []
        try:
            ngram_results = await self._search_relevant_cards(question, user_id, top_k=5)
        except Exception as e:
            logger.warning(f"n-gram 检索失败: {e}")

        # 5. RRF 融合三路结果
        fused_results = self._rrf_fusion(
            vector_results, bm25_results, ngram_results, k=60, top_k=5
        )

        # 6. 合并上下文
        context_parts: List[str] = []

        if fused_results:
            context_parts.append("=== 相关文档片段 ===")
            for item in fused_results:
                note_title = item.get("note_title") or item.get("title") or "未知来源"
                chapter_info = ""
                if item.get("chapter_title"):
                    chapter_info = f" (章节: {item['chapter_title']})"
                context_parts.append(
                    f"[来源: {note_title}{chapter_info}]\n{item['content']}"
                )

        context = "\n\n".join(context_parts)

        # 7. 构建引用来源（回查笔记标题）
        sources = []
        seen_notes = set()

        for item in fused_results:
            note_id = item.get("note_id")
            if note_id is None:
                continue
            if note_id in seen_notes:
                continue

            note_title = item.get("note_title")
            chapter_title = item.get("chapter_title")
            relevant_text = (item.get("content") or "")[:200]

            # note_title 可能为 None（来自 BM25 结果），回查笔记标题
            if note_title is None:
                session_factory = self._get_session_factory()
                async with session_factory() as session:
                    note_result = await session.execute(
                        select(Note).where(Note.id == note_id)
                    )
                    note = note_result.scalars().first()
                    note_title = note.title if note else "未知笔记"

            sources.append({
                "note_id": note_id,
                "note_title": note_title,
                "chapter_title": chapter_title,
                "relevant_text": relevant_text,
            })
            seen_notes.add(note_id)

        return {
            "context": context,
            "sources": sources,
            "provider": provider,
        }

    async def answer_question(
        self,
        question: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """
        RAG 问答完整流程（混合检索 + RRF 融合）

        在 retrieve_context() 检索结果基础上调用 LLM 生成回答：
        1. 调用 retrieve_context() 完成检索阶段（向量 + BM25 + n-gram + RRF 融合）
        2. 上下文为空时使用 LLM 自身知识回答（明确告知用户）
        3. 上下文非空时调用 llm_service.rag_answer() 基于上下文回答
        4. 返回回答 + 引用来源 + 提供商

        降级策略：
        - 向量编码/检索失败：仅使用 BM25 + n-gram
        - BM25 失败：仅使用 n-gram
        - 全部失败：使用 LLM 自身知识回答

        Args:
            question: 用户问题
            user_id: 用户 ID

        Returns:
            Dict: {"answer": str, "sources": list, "provider": str}
        """
        llm_service = LLMService()

        # 1. 检索阶段（不调用 LLM）
        retrieval = await self.retrieve_context(question, user_id)
        context = retrieval["context"]
        sources = retrieval["sources"]

        # 2. 上下文为空时使用 LLM 自身知识回答
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

        # 3. 调用 LLM 基于 context 生成回答
        answer = await llm_service.rag_answer(question, context)

        return {
            "answer": answer,
            "sources": sources,
            "provider": llm_service._provider,
        }
