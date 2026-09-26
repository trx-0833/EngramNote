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
- Celery 任务为同步函数；`encode_text` **不需要**事件循环（只做模型加载与编码，
  不碰数据库）。`embed_cards` 例外 —— 它要把向量写回库，因此用
  `tasks/loop.py` 的 `task_loop` + `run_async`（与 `clean_tasks` 同一套）
"""

from __future__ import annotations

import functools
import hashlib
import logging
from typing import Dict, List, Optional, Tuple


from .celery_app import celery_app
from .loop import run_async, task_loop
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
        KeyError: text_hash 未在注册表中注册时
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


#: `embed_cards` 单批处理多少张卡。
#:
#: 为什么是 200 而不是全量：编码本身约 0.37 秒/张（实测 1181 张 275 秒），
#: 一次 200 张约 75 秒 —— 与 `MAX_CARDS_FOR_SUGGEST` 同量级，任务不会长到
#: 让 worker 看起来像卡死。调用方循环投递即可推进全量。
EMBED_CARDS_BATCH = 200


@celery_app.task(acks_late=True, task_reject_on_worker_lost=True)
def embed_cards(user_id: str, limit: int = EMBED_CARDS_BATCH) -> Dict[str, int]:
    """给**还没有向量**的卡片补向量并写回库（关系判定提速的前提）

    ## 为什么需要这个任务

    关系建议原本每次点击都现算向量：200 张 73 秒、全量 1181 张 275 秒，
    而 `POST /graph/suggest` 的 Celery `task.get` 超时是 180 秒 ——
    于是候选池被死死压在 200 张，**904 张卡片根本进不了候选池**
    （孤点 1061 张里占 85.2%）。

    把向量存进 `knowledge_cards.embedding` 之后，每次点击只做 numpy
    （1181² 余弦矩阵实测 10 ms），候选池才有可能放开到全量。

    ## 只处理 `has_embedding=False` 的行

    已嵌入的卡片**不重算** —— 嵌入模型换了要重算时，把 `has_embedding`
    置回 False（或按 `embedding_model` 过滤）另行处理，不在这个任务里做。
    重算全量是 275 秒，不该混进"补缺失"这条日常路径。

    ## 向量文本口径必须与 `graph_service._build_card_embedding_text` 一致

    否则库里存的向量与"现算"的向量不是同一个东西，相似度就没有可比性。
    这里**直接 import 那个函数**，不另写一份。

    Args:
        user_id: 用户 ID
        limit: 本批最多处理多少张（默认 200）

    Returns:
        dict: `{"scanned": 扫描到的待嵌入卡片数, "embedded": 实际写入数,
               "failed": 编码失败数, "remaining": 本批之后还剩多少}
    """
    from ..services.graph_service import _build_card_embedding_text

    with task_loop("embed_cards"):
        return run_async(_embed_cards_async(user_id, limit, _build_card_embedding_text))


async def _embed_cards_async(user_id: str, limit: int, build_text) -> Dict[str, int]:
    """`embed_cards` 的异步主体（要读写库，因此必须跑在任务事件循环里）"""
    from sqlalchemy import func, select, update

    from ..models.chunk import pack_vector
    from ..models.knowledge_card import KnowledgeCard
    from ..models.note import Note
    from .common import get_sync_session

    session_factory = get_sync_session()
    async with session_factory() as session:
        rows = (await session.execute(
            select(KnowledgeCard.id, KnowledgeCard.title, KnowledgeCard.content)
            .where(
                KnowledgeCard.user_id == user_id,
                KnowledgeCard.has_embedding.is_(False),
                Note.not_trashed(KnowledgeCard.note_id),
            )
            .order_by(KnowledgeCard.updated_at.desc())
            .limit(limit)
        )).all()

        if not rows:
            return {"scanned": 0, "embedded": 0, "failed": 0, "remaining": 0}

        texts = [build_text(title, content) for _cid, title, content in rows]
        vectors = encode_text(texts)
        model_name = _model_name()

        embedded = failed = 0
        # strict=True：`encode_text` 保证一条文本一个向量，长度不等说明任务坏了，
        # 宁可让任务报错，也不能悄悄少写几张卡（那会让它们永远没有向量）
        for (card_id, _title, _content), vec in zip(rows, vectors, strict=True):
            if not vec:
                failed += 1
                continue
            await session.execute(
                update(KnowledgeCard)
                .where(KnowledgeCard.id == card_id)
                .values(
                    embedding=pack_vector(vec),
                    embedding_dim=len(vec),
                    embedding_model=model_name,
                    has_embedding=True,
                )
            )
            embedded += 1
        await session.commit()

        remaining = (await session.execute(
            select(func.count()).select_from(KnowledgeCard).where(
                KnowledgeCard.user_id == user_id,
                KnowledgeCard.has_embedding.is_(False),
                Note.not_trashed(KnowledgeCard.note_id),
            )
        )).scalar_one()

    logger.info(
        "embed_cards: user=%s 扫描 %d 张 → 写入 %d、失败 %d，剩余 %d",
        user_id, len(rows), embedded, failed, remaining,
    )
    return {
        "scanned": len(rows),
        "embedded": embedded,
        "failed": failed,
        "remaining": remaining,
    }


def _model_name() -> Optional[str]:
    """**实际加载的**嵌入模型名（写进 `embedding_model` 列）

    ⚠️ 必须用 `loaded_model_name` 而不是 `settings.embedding_model` ——
    `EmbeddingService` 在**可用内存不足时会自动降级**
    （`embedding_service.py:66` 的 `_pick_embedding_model_name`：主模型
    `BAAI/bge-m3`（1024 维）→ 降级 `BAAI/bge-small-zh-v1.5`（512 维））。
    这个降级不是理论情况 —— 本机跑端到端验证时**真的触发了**
    （"可用内存不足（2.5GB < 4.0GB）"）。

    第一次实现取的是 `getattr(service, "model_name", None) or settings.embedding_model`，
    而 `EmbeddingService` 根本没有 `model_name` 这个属性（只有 `_model_name` 与
    属性 `loaded_model_name`），于是**每一行都写成了配置值** —— 库里 512 维的向量
    被标成 `BAAI/bge-m3`。这一列存在的意义就是"将来按模型重算"，
    记错等于把它废掉。
    """
    service = _get_embedding_service()
    return getattr(service, "loaded_model_name", None) or settings.embedding_model

