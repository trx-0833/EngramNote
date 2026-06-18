"""
笔记清洗异步任务模块

本模块定义了 Celery 异步任务，负责将已转换的笔记进行 AI 清洗处理。
清洗管道包括规则化清洗、文本分块、嵌入生成、去重检测和清洗副本生成。

主要职责：
- 定义 Celery 任务 clean_document_task
- 执行清洗核心逻辑（读取原文 → 规则清洗 → 分块 → 嵌入 → 去重 → 生成副本）
- 更新笔记状态（converted → cleaning → cleaned / cleaning_failed）
- 将清洗副本上传到对象存储
- 记录清洗统计信息到笔记元数据

设计决策：
- 清洗任务由转换完成后自动触发，也可由用户手动触发
- 清洗失败不影响原始 Markdown，仅标记状态为 failed
- 重复块用 HTML 注释标记而非删除，用户可随时恢复
- 清洗统计信息存储在笔记的 metadata_ 字段中
"""

from typing import Optional

import traceback

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from .celery_app import celery_app
from ..config import get_settings
from ..models.note import Note, NoteStatus

settings = get_settings()

# Celery 任务中需要独立的数据库连接
_clean_engine = None
_clean_session_factory = None


def _get_clean_session():
    """
    获取清洗任务专用的数据库会话工厂

    与 convert_tasks.py 中的模式一致，Celery worker 运行在独立进程中，
    需要创建自己的数据库连接。

    Returns:
        async_sessionmaker: 异步会话工厂
    """
    global _clean_engine, _clean_session_factory
    if _clean_session_factory is None:
        _clean_engine = create_async_engine(settings.get_database_url(), echo=False)
        _clean_session_factory = async_sessionmaker(_clean_engine, expire_on_commit=False)
    return _clean_session_factory


async def _get_note_status(note_id: str) -> Optional[NoteStatus]:
    """
    获取笔记当前状态

    用于在 Celery 任务中检查笔记是否已被用户停止清洗。

    Args:
        note_id: 笔记 ID

    Returns:
        NoteStatus 或 None（笔记不存在时）
    """
    session_factory = _get_clean_session()
    async with session_factory() as session:
        result = await session.execute(select(Note).where(Note.id == note_id))
        note = result.scalars().first()
        return note.status if note else None


async def _update_note_status(note_id: str, status: NoteStatus, error_message: Optional[str] = None, **kwargs):
    """
    更新笔记状态

    在 Celery worker 的独立数据库会话中更新笔记的状态和附加字段。

    Args:
        note_id: 笔记 ID
        status: 新的状态
        error_message: 错误信息（可选）
        **kwargs: 其他需要更新的字段
    """
    session_factory = _get_clean_session()
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


async def _clean_document(note_id: str):
    """
    执行文档清洗的核心逻辑

    完整流程：
    1. 从数据库获取笔记记录，校验状态
    2. 从对象存储读取原始 Markdown
    3. 规则化清洗（去空行、页眉页脚、水印等）
    4. 文本分块
    5. 生成嵌入向量
    6. 存储向量到 Chroma
    7. 查找重复块
    8. 生成清洗副本（重复块用 HTML 注释标记）
    9. 上传清洗副本到对象存储
    10. 更新笔记状态和元数据

    Args:
        note_id: 笔记 ID
    """
    from ..services.storage_service import (
        get_object_bytes,
        upload_bytes,
        ensure_buckets_exist,
    )
    from ..services.cleaning_service import (
        clean_rules,
        split_into_chunks,
        generate_clean_copy,
    )
    from ..services.embedding_service import EmbeddingService, VectorStore

    # 确保存储桶目录存在
    ensure_buckets_exist()

    # 1. 获取笔记记录
    session_factory = _get_clean_session()
    async with session_factory() as session:
        result = await session.execute(select(Note).where(Note.id == note_id))
        note = result.scalars().first()
        if not note:
            return
        original_md_path = note.original_md_path
        user_id = note.user_id

    if not original_md_path:
        await _update_note_status(
            note_id, NoteStatus.cleaning_failed,
            error_message="笔记缺少原始 Markdown 路径",
        )
        return

    # 2. 读取原始 Markdown
    try:
        md_bytes = get_object_bytes(settings.minio_bucket_markdown, original_md_path)
        original_text = md_bytes.decode("utf-8")
    except Exception as e:
        await _update_note_status(
            note_id, NoteStatus.cleaning_failed,
            error_message=f"读取原始 Markdown 失败: {str(e)}",
        )
        return

    if not original_text.strip():
        await _update_note_status(
            note_id, NoteStatus.cleaning_failed,
            error_message="原始 Markdown 内容为空",
        )
        return

    # 3. 规则化清洗
    cleaned_text, clean_stats = clean_rules(original_text)

    # 4. 文本分块
    chunks = split_into_chunks(cleaned_text)
    if not chunks:
        # 文本太短无法分块，直接保存清洗结果
        clean_md_path = original_md_path.rsplit("/", 1)[0] + "/clean.md"
        upload_bytes(
            settings.minio_bucket_markdown,
            clean_md_path,
            cleaned_text.encode("utf-8"),
            content_type="text/markdown; charset=utf-8",
        )
        await _update_note_status(
            note_id, NoteStatus.cleaned,
            clean_md_path=clean_md_path,
            metadata_={
                "clean_stats": clean_stats,
                "duplicate_blocks": 0,
                "total_chunks": 0,
            },
        )
        return

    # 5. 生成嵌入向量
    try:
        embedding_service = EmbeddingService()
        texts = [chunk["content"] for chunk in chunks]
        embeddings = embedding_service.encode(texts)
    except Exception as e:
        await _update_note_status(
            note_id, NoteStatus.cleaning_failed,
            error_message=f"嵌入向量生成失败: {str(e)}",
        )
        return

    # 6. 存储向量到 Chroma
    try:
        vector_store = VectorStore()
        vector_store.add_chunks(note_id, chunks, embeddings)
    except Exception as e:
        await _update_note_status(
            note_id, NoteStatus.cleaning_failed,
            error_message=f"向量存储失败: {str(e)}",
        )
        return

    # 7. 查找重复块
    try:
        duplicates = vector_store.find_duplicates(note_id)
    except Exception as e:
        # 去重失败不影响清洗，继续生成副本
        duplicates = []

    # 8. 生成清洗副本
    clean_copy, copy_stats = generate_clean_copy(original_text, cleaned_text, duplicates)

    # 9. 上传清洗副本到对象存储
    clean_md_path = original_md_path.rsplit("/", 1)[0] + "/clean.md"
    upload_bytes(
        settings.minio_bucket_markdown,
        clean_md_path,
        clean_copy.encode("utf-8"),
        content_type="text/markdown; charset=utf-8",
    )

    # 10. 更新笔记状态和元数据
    metadata = {
        "clean_stats": clean_stats,
        "copy_stats": copy_stats,
        "duplicate_blocks": len(duplicates),
        "total_chunks": len(chunks),
        "duplicates_detail": [
            {
                "block_index": d["block_index"],
                "duplicate_of": d["duplicate_of"],
                "similarity": round(d["similarity"], 4),
            }
            for d in duplicates
        ],
    }

    await _update_note_status(
        note_id, NoteStatus.cleaned,
        clean_md_path=clean_md_path,
        metadata_=metadata,
    )


@celery_app.task(bind=True, max_retries=2, default_retry_delay=60)
def clean_document_task(self, note_id: str):
    """
    Celery 任务：文档清洗

    作为 Celery 异步任务执行，由转换完成或用户手动触发。
    使用 asyncio.run() 在同步的 Celery 任务中运行异步的清洗逻辑。

    任务配置：
    - bind=True：可访问 self（任务实例），用于重试
    - max_retries=2：最多重试 2 次
    - default_retry_delay=60：重试间隔 60 秒

    Args:
        self: Celery 任务实例（bind=True 时自动传入）
        note_id: 笔记 ID
    """
    import asyncio
    import logging

    logger = logging.getLogger(__name__)

    try:
        # 检查笔记是否已被用户停止清洗
        current_status = asyncio.run(_get_note_status(note_id))
        if current_status == NoteStatus.cleaning_failed:
            logger.info(f"笔记已被用户停止清洗，跳过任务 (note_id={note_id})")
            return

        # 将状态更新为 cleaning
        # 手动触发时 API 层已预更新，此步为冗余但无害；
        # 自动触发（转换完成后）时 API 层未预更新，此步为必要
        try:
            asyncio.run(_update_note_status(note_id, NoteStatus.cleaning))
        except Exception:
            pass

        asyncio.run(_clean_document(note_id))
    except Exception as exc:
        logger.error(f"清洗任务异常 (note_id={note_id}): {exc}", exc_info=True)

        # 重试前检查：如果用户已停止清洗，不再重试
        try:
            current_status = asyncio.run(_get_note_status(note_id))
            if current_status == NoteStatus.cleaning_failed:
                logger.info(f"笔记已被用户停止清洗，放弃重试 (note_id={note_id})")
                return
        except Exception:
            pass  # 状态查询失败时仍尝试重试

        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            # 重试次数用尽，标记笔记为失败状态
            try:
                asyncio.run(_update_note_status(
                    note_id, NoteStatus.cleaning_failed,
                    error_message=f"清洗任务重试失败: {str(exc)}",
                ))
            except Exception as update_err:
                logger.error(f"更新笔记状态失败 (note_id={note_id}): {update_err}")
