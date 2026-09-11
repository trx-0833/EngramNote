"""
Chunk 索引服务（overhaul-plan 阶段 2.2′ / 2.3）
==============================================

把一篇笔记的 Markdown 切成 chunk 并落库，使"检索单元"保持与原文同步。

## 为什么必须是服务、并由清洗流程调用

`chunks` 表是**派生数据**：它由 `notes` 的 Markdown 算出。派生数据最容易
出现的失效方式是"原文变了、索引没重建"—— 这正是 A-18
（"用户编辑笔记后向量永久陈旧"）那一类缺陷。

先前只有 `scripts/index_chunks.py` 一个手动入口。手动入口意味着：
**新笔记清洗完成后不会进索引**，直到有人记得去跑脚本。
结果是"新上传的资料问不出来"，而用户完全不知道原因。

因此把逻辑收进服务，由 `clean_tasks` 在清洗完成后直接调用。
脚本退化为它的薄 CLI 外壳。

## 与向量嵌入的分工

本服务只负责**文本与定位信息**（A 半），不写向量（B 半，需加载 4.3GB 模型，
不能放在清洗流程里同步做）。`has_embedding=False` 的行需由
`scripts/embed_chunks.py` 补嵌入。

这个分工是有意的：清洗任务是高并发的用户路径，嵌入是一次性的重量级操作。
`_get_user_chunks` 只取 `has_embedding=True` 的行，因此未嵌入的新笔记
暂时只被 BM25 通道检索到 —— **降级可用，而不是完全查不到**。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.chunk import Chunk

logger = logging.getLogger(__name__)


def chunk_hash(chunks: List[Dict[str, Any]], source_path: Optional[str]) -> str:
    """整篇的分块指纹（内容 + 来源对象名 + 定位区间）

    必须覆盖来源对象名：`clean_md_path` 从 original 换成 clean 副本时，
    内容可能碰巧相同，但偏移的**参照物**变了。也必须覆盖定位区间：
    同样的文本在不同位置出现，偏移不同，索引必须重建。
    """
    digest = hashlib.sha256()
    digest.update((source_path or "").encode())
    for c in chunks:
        digest.update(c["content"].encode())
        digest.update(f"{c['char_start']}:{c['char_end']}".encode())
    return digest.hexdigest()


async def index_note_chunks(
    db: AsyncSession,
    *,
    note_id: str,
    user_id: str,
    text: str,
    source_path: Optional[str],
    chunk_size: int,
    only_stale: bool = False,
) -> int:
    """为单篇笔记重建 chunk 行，返回写入条数

    ## 为什么"先删后插"而不是 upsert

    分块结果的长度会变（原文被编辑后 chunk 数可能减少）。只做 upsert
    会留下上一轮的**多余行**，那些行的偏移指向已不存在的位置，
    却仍会被检索到 —— 比"少几行"危险得多。先删后插在同一个事务里完成，
    不会出现"半新半旧"。

    ## 不变量校验放在写入之前

        text[char_start:char_end] == content

    这条不变量在开发中被违反过**两次**（各 1318 个分段，见附录 L.2）。
    落库比"分段算错"严重：偏移一旦写进库就成了长期契约，
    2.7 的回跳会一直按它切片，而错误是静默的。
    因此校验不过**拒绝整篇落库**（返回 0 并记 error），不跳过单行 ——
    L.2 的两次失效都是整篇性系统漂移，只丢一行会掩盖问题。

    Args:
        db: 数据库会话（**不在此提交**，由调用方决定事务边界）
        note_id / user_id: 归属
        text: 笔记 Markdown（应为 `source_path` 指向的那份内容）
        source_path: 来源对象名（clean_md_path 优先）
        chunk_size: 分块字符上限
        only_stale: 指纹未变时跳过（避免重复写同内容）

    Returns:
        int: 写入的 chunk 条数；因不变量失败或指纹未变时为 0
    """
    from .markdown_segmenter import segment_with_offsets, to_retrieval_chunks

    chunks = to_retrieval_chunks(segment_with_offsets(text, chunk_size))
    fingerprint = chunk_hash(chunks, source_path)

    if only_stale:
        existing = (await db.execute(
            select(Chunk.content_hash).where(
                Chunk.note_id == note_id, Chunk.index == 0
            )
        )).scalars().first()
        have = (await db.execute(
            select(Chunk.id).where(Chunk.note_id == note_id)
        )).scalars().all()
        if existing == fingerprint and len(have) == len(chunks):
            return 0

    # 写入前自证不变量
    for c in chunks:
        if text[c["char_start"]:c["char_end"]] != c["content"]:
            logger.error(
                "拒绝写入 chunk：不变量被破坏 note_id=%s idx=%s chars=%s-%s",
                note_id, c["index"], c["char_start"], c["char_end"],
            )
            return 0

    await db.execute(delete(Chunk).where(Chunk.note_id == note_id))
    db.add_all([
        Chunk(
            user_id=user_id,
            note_id=note_id,
            index=c["index"],
            content=c["content"],
            char_start=c["char_start"],
            char_end=c["char_end"],
            heading_path=c["heading_context"] or None,
            line_start=c["start_line"],
            line_end=c["end_line"],
            char_count=c["char_count"],
            source_md_path=source_path,
            content_hash=fingerprint,
            has_embedding=False,
        )
        for c in chunks
    ])
    return len(chunks)


async def index_note_from_storage(
    db: AsyncSession,
    *,
    note_id: str,
    user_id: str,
    clean_md_path: Optional[str],
    original_md_path: Optional[str],
    chunk_size: int,
) -> int:
    """从对象存储读取 Markdown 后建索引（清洗完成后的便捷入口）

    读取顺序与检索一致：clean 副本优先，缺失时退回 original。
    Markdown 读不到时返回 0 并记 warning —— **不抛异常**：
    清洗流程的主产物是 clean.md 与状态，索引失败不应让整个清洗任务失败
    （检索层会退化为只用 BM25 通道，属于可用状态）。
    """
    from ..config import get_settings
    from .storage_service import get_object_bytes

    settings = get_settings()
    for path in (clean_md_path, original_md_path):
        if not path:
            continue
        try:
            data = get_object_bytes(settings.minio_bucket_markdown, path)
            text = data.decode("utf-8")
        except Exception as exc:
            logger.warning("建 chunk 索引时读取 Markdown 失败 path=%s: %s", path, exc)
            continue
        if not text.strip():
            continue
        return await index_note_chunks(
            db, note_id=note_id, user_id=user_id, text=text,
            source_path=path, chunk_size=chunk_size,
        )
    logger.warning("无可用的 Markdown，跳过 chunk 索引 note_id=%s", note_id)
    return 0


async def get_user_chunks(db: AsyncSession, user_id: str) -> List[Dict[str, Any]]:
    """取该用户的 chunk 语料（供 RAG 的 BM25 索引使用）

    只取 `has_embedding=True` 的行吗？**不** —— BM25 是纯词法检索，
    不需要向量。因此未嵌入的新 chunk 依然可被 BM25 检索到，
    这让"清洗完成但尚未跑嵌入"的窗口期仍可用（降级而非不可用）。

    回收站笔记的 chunk 不参与检索，与卡片语料的处理保持一致。
    """
    from ..models.note import Note

    result = await db.execute(
        select(Chunk).where(
            Chunk.user_id == user_id,
            select(Note.id).where(
                Note.id == Chunk.note_id, Note.trashed_at.is_(None)
            ).exists(),
        )
    )
    return [
        {
            "chunk_id": c.id,
            "note_id": c.note_id,
            "index": c.index,
            "title": c.heading_path or "",
            "content": c.content or "",
            "chapter_title": c.heading_path,
            "char_start": c.char_start,
            "char_end": c.char_end,
            "heading_path": c.heading_path,
            "line_start": c.line_start,
            "line_end": c.line_end,
        }
        for c in result.scalars().all()
    ]
