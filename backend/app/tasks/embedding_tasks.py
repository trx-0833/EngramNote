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
- Celery 任务为同步函数；本模块**不需要**事件循环（只做模型加载与编码，
  不碰数据库），因此没有 asyncio 调用
"""

from __future__ import annotations

import functools
import hashlib
import logging
from typing import Dict, List, Tuple


from .celery_app import celery_app
from ..config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

# 每篇笔记在向量检索阶段取的候选块数。
# 旧值为 min(3, count)：一篇 300 页文档约 600 个 chunk，每篇笔记只捞 3 个，
# 原文级细节几乎不可能命中。改为按候选集召回后再由相似度地板过滤，
# 「召回广 + 过滤严」比「召回窄 + 不过滤」更接近正确做法。
_VECTOR_CANDIDATES_PER_NOTE = 8

# 模块级单例：缓存 EmbeddingService 实例
# 避免每次任务调用都重新加载 ~2.2GB 的 BGE-M3 模型
_embedding_service = None

# 哈希到原文的注册表，配合 lru_cache 使用
# lru_cache 以 text_hash 为键，需要通过注册表回查原文进行编码
_hash_to_text_registry: Dict[str, str] = {}


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

