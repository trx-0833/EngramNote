"""
嵌入模型异步任务模块

本模块将 BGE-M3 嵌入模型加载与向量搜索隔离到 Celery worker 进程中执行，
避免在 FastAPI 主进程中加载大模型导致段错误（0xC0000005）。

主要职责：
- 在 Celery worker 中加载 BGE-M3 模型并编码文本
- 在 Celery worker 中跨用户笔记 collection 执行向量搜索
- 通过模块级单例缓存模型实例，避免重复加载 ~2.2GB 模型
- 通过进程级 LRU 缓存避免重复编码相同文本

设计决策：
- 嵌入模型必须在 Celery worker 中加载，不在 FastAPI 主进程中加载
- 使用模块级单例 _embedding_service 缓存 EmbeddingService 实例
- 使用 functools.lru_cache(maxsize=1024) 缓存单条文本的编码结果，
  缓存键为 sha256(text)[:16]，减少内存占用并避免重复计算
- 任务使用 acks_late=True + task_reject_on_worker_lost=True 确保可靠性，
  worker 崩溃时任务会被重新投递
- Celery 任务为同步函数，内部通过 asyncio.run() 调用异步数据库逻辑
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from .celery_app import celery_app
from ..config import get_settings
from ..models.note import Note

settings = get_settings()
logger = logging.getLogger(__name__)

# 模块级单例：缓存 EmbeddingService 实例
# 避免每次任务调用都重新加载 ~2.2GB 的 BGE-M3 模型
_embedding_service = None

# 哈希到原文的注册表，配合 lru_cache 使用
# lru_cache 以 text_hash 为键，需要通过注册表回查原文进行编码
_hash_to_text_registry: Dict[str, str] = {}

# F-27：会话工厂收敛到 tasks/common.py（含 SQLite PRAGMA）
from .common import get_sync_session as _get_sync_session


def _get_embedding_service():
    """
    延迟初始化嵌入服务（模块级单例）

    首次调用时创建 EmbeddingService 实例并缓存到模块级变量，
    后续调用直接复用，避免重复加载模型。
    EmbeddingService 内部也会缓存 SentenceTransformer 模型实例。

    Returns:
        EmbeddingService: 嵌入服务实例
    """
    global _embedding_service
    if _embedding_service is None:
        # 延迟导入，避免在模块加载时就触发 sentence-transformers 依赖检查
        from ..services.embedding_service import EmbeddingService
        _embedding_service = EmbeddingService()
    return _embedding_service


@functools.lru_cache(maxsize=1024)
def _cached_encode(text_hash: str) -> Tuple[List[float]]:
    """
    以 sha256(text)[:16] 为键的缓存编码

    使用 functools.lru_cache 装饰器，进程级缓存最近 1024 条文本的编码结果。
    通过 text_hash 作为键，避免在缓存键中存储完整文本，减少内存占用。
    原文通过 _hash_to_text_registry 注册表回查。

    Args:
        text_hash: sha256(text)[:16] 的十六进制字符串

    Returns:
        Tuple[List[float]]: 单元素元组，包含嵌入向量
            （使用元组包装以确保可哈希和 lru_cache 兼容）

    Raises:
        KeyError: 当 text_hash 未在注册表中注册时
    """
    text = _hash_to_text_registry.get(text_hash)
    if text is None:
        raise KeyError(f"未注册的文本哈希: {text_hash}")
    service = _get_embedding_service()
    embeddings = service.encode([text])
    return (embeddings[0],)


@celery_app.task(acks_late=True, task_reject_on_worker_lost=True)
def encode_text(texts: List[str]) -> List[List[float]]:
    """
    Celery 任务：将文本列表编码为嵌入向量列表

    在 Celery worker 进程中加载 BGE-M3 模型，避免在 FastAPI 主进程中
    加载导致段错误。使用进程级 LRU 缓存避免重复编码相同文本。

    任务配置：
    - acks_late=True：任务执行完成后才确认，避免 worker 崩溃时任务丢失
    - task_reject_on_worker_lost=True：worker 异常退出时重新投递任务

    Args:
        texts: 待编码的文本列表

    Returns:
        List[List[float]]: 嵌入向量列表，每个向量是一个浮点数列表，
            顺序与输入 texts 一致。编码失败的文本返回空列表。
    """
    if not texts:
        return []

    results: List[List[float]] = []
    for text in texts:
        # 以 sha256(text)[:16] 作为缓存键
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        # 注册哈希到原文的映射（供 _cached_encode 回查）
        _hash_to_text_registry[text_hash] = text
        try:
            embedding = _cached_encode(text_hash)
            results.append(embedding[0])
        except Exception as e:
            logger.warning(f"缓存编码失败，回退到直接编码: {e}")
            try:
                service = _get_embedding_service()
                emb = service.encode([text])
                results.append(emb[0] if emb else [])
            except Exception as fallback_err:
                logger.error(f"直接编码也失败: {fallback_err}")
                results.append([])
    return results


def _collection_matches_current_model(collection) -> bool:
    """
    判断 collection 的向量是否与当前加载的嵌入模型一致

    跨模型一致性：不同嵌入模型向量维度不同（bge-m3=1024、bge-small-zh-v1.5=512），
    混查会导致维度不匹配报错或相似度无意义。

    无 embedding_model 元数据（历史数据）或模型尚未加载时视为一致，
    由查询阶段的维度异常兜底跳过。

    Args:
        collection: Chroma collection 对象

    Returns:
        bool: 模型一致（或无记录可比对）返回 True
    """
    from ..services.embedding_service import EmbeddingService

    collection_meta = collection.metadata or {}
    stored_model = collection_meta.get("embedding_model")
    if not stored_model:
        return True  # 旧数据无记录：尝试查询，维度错误由下层异常兜底
    current_model = EmbeddingService().loaded_model_name
    if not current_model:
        return True  # 模型未加载：无从比对，交由查询阶段决定
    return stored_model == current_model


async def _search_vectors_async(
    user_id: str,
    question_embedding: List[float],
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """
    异步执行向量搜索

    遍历用户所有笔记的向量集合，对每个集合进行相似度搜索，
    合并结果后按相似度排序取 top_k。

    Args:
        user_id: 用户 ID
        question_embedding: 问题的嵌入向量
        top_k: 返回最相关的 top_k 个结果

    Returns:
        List[Dict]: 相关文本块列表，每个包含：
            - note_id: 笔记 ID
            - note_title: 笔记标题
            - content: 文本内容
            - similarity: 相似度分数（0-1）
            - block_index: 块索引
    """
    # 延迟导入，避免在 FastAPI 主进程中通过模块加载间接引入模型依赖
    from ..services.embedding_service import VectorStore

    vector_store = VectorStore()

    # 获取用户所有笔记
    session_factory = _get_sync_session()
    async with session_factory() as session:
        result = await session.execute(
            select(Note).where(Note.user_id == user_id)
        )
        notes = result.scalars().all()

    all_results: List[Dict[str, Any]] = []

    for note in notes:
        try:
            def _search_note_collection(
                note_id: str = note.id,
                note_title: str = note.title,
            ) -> List[Dict[str, Any]]:
                try:
                    vector_store._ensure_client()
                    collection_name = vector_store._get_collection_name(note_id)
                    collection = vector_store._client.get_collection(collection_name)
                except Exception:
                    return []

                # 跨模型一致性：collection 记录的生成模型与当前加载模型不一致时跳过。
                # 旧数据无 embedding_model 元数据时仍尝试查询（维度不匹配由下层异常兜底跳过）。
                if not _collection_matches_current_model(collection):
                    collection_meta = collection.metadata or {}
                    logger.warning(
                        f"跳过向量集合 {collection_name}: 存储模型 "
                        f"{collection_meta.get('embedding_model')} 与当前模型 "
                        f"{EmbeddingService().loaded_model_name} 不一致"
                    )
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

                results: List[Dict[str, Any]] = []
                for i, doc in enumerate(query_results["documents"][0]):
                    distance = query_results["distances"][0][i]
                    similarity = 1.0 / (1.0 + distance)
                    results.append({
                        "note_id": note_id,
                        "note_title": note_title,
                        "content": doc,
                        "similarity": similarity,
                        "block_index": query_results["metadatas"][0][i].get(
                            "block_index", 0
                        ),
                    })
                return results

            note_results = await asyncio.to_thread(_search_note_collection)
            all_results.extend(note_results)
        except Exception as e:
            logger.warning(f"搜索笔记 {note.id} 的向量数据失败: {e}")
            continue

    all_results.sort(key=lambda x: x["similarity"], reverse=True)
    return all_results[:top_k]


@celery_app.task(acks_late=True, task_reject_on_worker_lost=True)
def search_vectors(
    user_id: str,
    question_embedding: List[float],
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """
    Celery 任务：在用户的笔记向量集合中搜索与问题向量相关的文本块

    跨用户所有笔记的 collection 进行向量相似度搜索，合并结果按相似度排序。
    模型加载和向量检索均在 Celery worker 进程中完成。

    任务配置：
    - acks_late=True：任务执行完成后才确认
    - task_reject_on_worker_lost=True：worker 异常退出时重新投递任务

    Args:
        user_id: 用户 ID
        question_embedding: 问题的嵌入向量
        top_k: 返回最相关的 top_k 个结果，默认 5

    Returns:
        List[Dict]: 相关文本块列表，每个包含：
            - note_id: 笔记 ID
            - note_title: 笔记标题
            - content: 文本内容
            - similarity: 相似度分数（0-1）
            - block_index: 块索引
    """
    return asyncio.run(
        _search_vectors_async(user_id, question_embedding, top_k)
    )
