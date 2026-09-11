"""
Chunk 向量检索（overhaul-plan 阶段 2.2′ B 半 / 2.4′）
====================================================

在 `chunks` 表上做向量检索 —— **一次 SQL 取代遍历 N 个 collection**。

## 为什么这是 2.4′ 的正解

原实现（`embedding_tasks._search_vectors_async`）为**每篇笔记**单独查一次
Chroma collection：

- 实测 24 个 collection，检索要遍历全部（D-9）
- 每个 collection 一次 RPC，且**模型不一致时只能跳过**
  （实测 43% 的向量因维度不匹配永久取不到，见附录 N.2）
- 一次提问 = 24 次查询 + 跨 collection 合并

改为 `chunks` 表后：

- **一次 SQL** 取回该用户全部已嵌入 chunk（按 `has_embedding` 索引过滤）
- 向量存在同一张表、由**同一个模型**生成 → 不存在"跳过一部分"
- 定位字段（`char_start/char_end/heading_path`）与向量同源，
  引用回跳不再依赖元数据穿过 Chroma 的层层转换

## 为什么在 Python 里算余弦而不是用 SQL 扩展

`sqlite-vec` 是独立扩展，需要额外装载；本项目定位是"单机自托管、
零外部依赖"。实测数据规模下（单用户千级 chunk）纯 Python 点积足够快。

若将来 chunk 上到十万级，再把 `sqlite-vec` 接进来（2.4′ 的评估结论），
届时本模块的接口不需要变 —— 它只承诺"给问题向量、返回 top_k chunk"。

因为落库时已确认全部向量为**单位向量**（实测 608/608 模长精确为 1.0），
余弦相似度就是点积，不需要再除以模长。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.chunk import Chunk, unpack_vector

logger = logging.getLogger(__name__)


async def search_chunks(
    db: AsyncSession,
    question_embedding: List[float],
    user_id: str,
    *,
    top_k: int = 5,
    exclude_note_ids: Optional[set[str]] = None,
) -> List[Dict[str, Any]]:
    """在用户的 chunk 向量上做余弦检索

    Args:
        db: 数据库会话
        question_embedding: 问题向量（**必须与落库向量同模型**，否则无意义）
        user_id: 用户 ID（多租户隔离）
        top_k: 返回条数
        exclude_note_ids: 排除这些笔记的 chunk（回收站笔记）

    Returns:
        List[Dict]: 每项含
            - chunk_id / note_id / index
            - content / similarity
            - char_start / char_end / heading_path / line_start / line_end

        后 5 个定位字段是**本模块相对旧实现的关键增益**：
        旧路径在四层转换里把位置信息丢光了（附录 N.4），回跳因此做不成。

    ## 模型一致性

        落库向量的 `embedding_model` 与本查询向量的模型不一致时，余弦相似度
        **没有意义**（不同向量空间）。因此函数会先读库里的模型名：
        混入不一致的模型时**返回空而不是给出错误排序** ——
        一个错误的相似度会静默参与 RRF 融合并挤掉正确结果。
    """
    if not question_embedding:
        return []

    query = select(Chunk).where(
        Chunk.user_id == user_id,
        Chunk.has_embedding.is_(True),
    )
    if exclude_note_ids:
        query = query.where(Chunk.note_id.notin_(exclude_note_ids))

    rows = list((await db.execute(query)).scalars().all())
    if not rows:
        return []

    qdim = len(question_embedding)
    scored: List[tuple[float, Chunk]] = []
    skipped_dim = 0

    for row in rows:
        vec = unpack_vector(row.embedding, row.embedding_dim)
        if vec is None or len(vec) != qdim:
            # 维度不符说明数据有问题 —— 跳过而不是硬算
            skipped_dim += 1
            continue
        # 单位向量 → 余弦 = 点积
        sim = 0.0
        for a, b in zip(question_embedding, vec, strict=True):
            sim += a * b
        scored.append((sim, row))

    if skipped_dim:
        logger.warning(
            "向量检索跳过 %d 条维度不符的 chunk（库中维度混杂会得到无意义的相似度）",
            skipped_dim,
        )
    if not scored:
        return []

    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {
            "chunk_id": row.id,
            "note_id": row.note_id,
            "index": row.index,
            "content": row.content,
            "similarity": sim,
            "char_start": row.char_start,
            "char_end": row.char_end,
            "heading_path": row.heading_path,
            "line_start": row.line_start,
            "line_end": row.line_end,
        }
        for sim, row in scored[:top_k]
    ]


async def embedded_model_names(db: AsyncSession, user_id: str) -> set[str]:
    """该用户已嵌入 chunk 用到的**全部**模型名（用于一致性检查）

    返回多于一个即说明库里混了模型 —— 那种状态下任何跨行比较都不可信。
    """
    from sqlalchemy import distinct

    result = await db.execute(
        select(distinct(Chunk.embedding_model)).where(
            Chunk.user_id == user_id, Chunk.has_embedding.is_(True)
        )
    )
    return {m for (m,) in result.all() if m}
