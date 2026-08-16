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

import os
import time
import logging

from celery.exceptions import Retry

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from .celery_app import celery_app
from ..config import get_settings
from ..models.note import Note, NoteStatus
from ..services import vault_path
from ..services.vault_meta import write_note_meta

settings = get_settings()
logger = logging.getLogger(__name__)

# F-27：会话工厂/状态更新/状态查询收敛到 tasks/common.py
from .common import (
    get_sync_session as _get_clean_session,
    update_note_status as _update_note_status,
    get_note_status as _get_note_status,
)


async def _is_cleaning_stopped(note_id: str) -> bool:
    """
    检查笔记是否已被用户停止清洗

    在清洗任务各阶段边界调用，若笔记状态已被 stop API 置为
    cleaning_failed，则提前终止，避免继续执行耗时步骤并在结尾覆盖状态。

    Args:
        note_id: 笔记 ID

    Returns:
        bool: 用户已停止清洗返回 True；笔记不存在时返回 False
    """
    current_status = await _get_note_status(note_id)
    return current_status == NoteStatus.cleaning_failed


def _clean_md_path_from_original(original_md_path: str) -> str:
    """
    由原始 Markdown 对象名推导清洗副本对象名

    命名关联法则：{P}/output/markdown/{base}.md → {P}/output/markdown/{base}.clean.md

    Args:
        original_md_path: 原始 Markdown 对象名

    Returns:
        str: 清洗副本对象名
    """
    parts = original_md_path.split("/")
    prefix = "/".join(parts[:2])
    base = os.path.splitext(parts[-1])[0]
    return vault_path.clean_object(prefix, base)


async def _create_auto_clean_version(note_id: str, user_id: str, clean_md_path: str) -> None:
    """
    在清洗覆盖现有 clean.md 前，为旧内容创建 auto_clean 版本快照

    若 clean.md 尚不存在（首次清洗），则跳过版本创建。
    版本创建失败仅记录警告日志，不阻塞清洗主流程。

    Args:
        note_id: 笔记 ID
        user_id: 用户 ID
        clean_md_path: 即将被覆盖的 clean.md 存储路径
    """
    from ..services.storage_service import get_object_bytes
    from ..services.version_service import version_service
    from ..models.note_version import VersionSource

    # 先尝试读取现有 clean.md 内容
    try:
        old_bytes = get_object_bytes(settings.minio_bucket_markdown, clean_md_path)
        old_content = old_bytes.decode("utf-8")
    except Exception:
        # 文件不存在（首次清洗），无需创建版本
        return

    if not old_content:
        return

    # 创建版本快照
    session_factory = _get_clean_session()
    try:
        async with session_factory() as session:
            await version_service.create_version(
                note_id=note_id,
                user_id=user_id,
                content=old_content,
                source=VersionSource.AUTO_CLEAN.value,
                db=session,
            )
    except Exception as e:
        logger.warning(
            f"创建清洗版本快照失败，继续执行清洗主流程: note_id={note_id[:8]}, err={e}"
        )


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
    start_time = time.monotonic()
    logger.info("文档清洗开始: note_id=%s", note_id)

    # 检查笔记是否已被用户删除（状态为 failed 且 error_message 为 "用户手动删除"）
    session_factory = _get_clean_session()
    async with session_factory() as session:
        note = await session.get(Note, note_id)
        if not note or (note.status == NoteStatus.failed and note.error_message == "用户手动删除"):
            logger.info(f"笔记 {note_id} 已被用户删除，跳过清洗任务")
            return
    from ..services.storage_service import (
        get_object_bytes,
        upload_bytes,
        ensure_buckets_exist,
    )
    from ..services.cleaning_service import (
        clean_rules,
        split_into_chunks,
        generate_clean_copy,
        find_duplicates_lightweight,
    )
    from ..services.embedding_service import (
        EmbeddingService,
        VectorStore,
        get_available_memory_gb,
    )

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

    # 检查用户是否已停止清洗
    if await _is_cleaning_stopped(note_id):
        logger.info(f"用户已停止清洗，跳过清洗流程 (note_id={note_id})")
        return

    # 3. 规则化清洗
    cleaned_text, clean_stats = clean_rules(original_text)

    # 4. 文本分块
    chunks = split_into_chunks(cleaned_text)
    if not chunks:
        # 检查用户是否已停止清洗（短文本直接保存前）
        if await _is_cleaning_stopped(note_id):
            logger.info(f"用户已停止清洗，跳过短文本保存 (note_id={note_id})")
            return
        # 文本太短无法分块，直接保存清洗结果
        clean_md_path = _clean_md_path_from_original(original_md_path)
        # 覆盖前为旧 clean.md 创建版本快照（首次清洗时跳过）
        await _create_auto_clean_version(note_id, user_id, clean_md_path)
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
                "dedup_mode": "none",
            },
        )
        return

    # 检查用户是否已停止清洗（嵌入生成前，避免加载模型耗时）
    if await _is_cleaning_stopped(note_id):
        logger.info(f"用户已停止清洗，跳过嵌入生成 (note_id={note_id})")
        return

    # 5. 生成嵌入向量
    # 失败时降级为无模型文本去重（dedup_mode=lightweight），保证清洗不整体失败
    embeddings = None
    embedding_model_name = None
    dedup_mode = "embedding"
    try:
        embedding_service = EmbeddingService()
        texts = [chunk["content"] for chunk in chunks]
        embeddings = embedding_service.encode(texts)
        embedding_model_name = embedding_service.loaded_model_name
    except Exception as e:
        dedup_mode = "lightweight"
        logger.error(
            "嵌入向量生成失败，降级为无模型文本去重: note_id=%s, err=%s, 可用内存=%.1fGB",
            note_id, e, get_available_memory_gb(),
        )

    # 检查用户是否已停止清洗（向量存储前）
    if await _is_cleaning_stopped(note_id):
        logger.info(f"用户已停止清洗，跳过向量存储 (note_id={note_id})")
        return

    if embeddings is not None:
        # 6. 存储向量到 Chroma（失败不影响清洗，降级文本去重）
        vector_store = None
        try:
            vector_store = VectorStore()
            vector_store.add_chunks(
                note_id, chunks, embeddings,
                embedding_model=embedding_model_name,
            )
        except Exception as e:
            logger.error(
                f"向量存储失败，降级为无模型文本去重: note_id={note_id}, err={e}"
            )

        # 7. 查找重复块
        if vector_store is not None:
            try:
                duplicates = vector_store.find_duplicates(note_id)
            except Exception as e:
                # 去重失败不影响清洗，继续生成副本
                logger.warning(f"向量去重失败，跳过去重: note_id={note_id}, err={e}")
                duplicates = []
        else:
            duplicates = find_duplicates_lightweight(chunks)
            dedup_mode = "lightweight"
    else:
        # 7b. 无模型兜底去重（嵌入模型不可用，如内存不足）
        try:
            duplicates = find_duplicates_lightweight(chunks)
        except Exception as e:
            await _update_note_status(
                note_id, NoteStatus.cleaning_failed,
                error_message=(
                    f"嵌入向量生成失败且文本去重兜底也失败: {str(e)}。"
                    f"当前可用内存约 {get_available_memory_gb():.1f}GB，"
                    f"请关闭部分程序释放内存或增大 Windows 页面文件后重试"
                ),
            )
            return

    # 8. 生成清洗副本
    clean_copy, copy_stats = generate_clean_copy(original_text, cleaned_text, duplicates)

    # 检查用户是否已停止清洗（上传清洗副本前）
    if await _is_cleaning_stopped(note_id):
        logger.info(f"用户已停止清洗，跳过清洗副本上传 (note_id={note_id})")
        return

    # 9. 上传清洗副本到对象存储
    clean_md_path = _clean_md_path_from_original(original_md_path)
    # 覆盖前为旧 clean.md 创建版本快照（首次清洗时跳过）
    await _create_auto_clean_version(note_id, user_id, clean_md_path)
    upload_bytes(
        settings.minio_bucket_markdown,
        clean_md_path,
        clean_copy.encode("utf-8"),
        content_type="text/markdown; charset=utf-8",
    )

    # 10. 更新笔记状态和元数据
    # 为每个重复对保存两个块的文本内容（content=重复块，original_content=被重复的保留块），
    # 供前端展示与人工核对（旧版本清洗的笔记无此字段，重新清洗后补齐）
    chunk_by_index = {chunk["index"]: chunk for chunk in chunks}
    duplicates_detail = []
    for d in duplicates:
        entry = {
            "block_index": d["block_index"],
            "duplicate_of": d["duplicate_of"],
            "similarity": round(d["similarity"], 4),
        }
        chunk = chunk_by_index.get(d["block_index"])
        if chunk is not None:
            entry["content"] = chunk["content"]
            # F-11 修复：记录重复块的精确行区间，供恢复/删除按行定位
            entry["start_line"] = chunk.get("start_line")
            entry["end_line"] = chunk.get("end_line")
        original_chunk = chunk_by_index.get(d["duplicate_of"])
        if original_chunk is not None:
            entry["original_content"] = original_chunk["content"]
        duplicates_detail.append(entry)

    metadata = {
        "clean_stats": clean_stats,
        "copy_stats": copy_stats,
        "duplicate_blocks": len(duplicates),
        "total_chunks": len(chunks),
        "dedup_mode": dedup_mode,
        "duplicates_detail": duplicates_detail,
    }
    # 记录实际使用的嵌入模型（降级时为用户识别去重质量提供依据）
    if embedding_model_name:
        metadata["embedding_model"] = embedding_model_name

    # 检查用户是否已停止清洗（写入最终状态前）
    if await _is_cleaning_stopped(note_id):
        logger.info(f"用户已停止清洗，跳过最终状态写入 (note_id={note_id})")
        return

    await _update_note_status(
        note_id, NoteStatus.cleaned,
        clean_md_path=clean_md_path,
        metadata_=metadata,
    )

    elapsed = time.monotonic() - start_time
    logger.info("文档清洗完成: note_id=%s, elapsed=%.1fs", note_id, elapsed)


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
        except Retry:
            # 正常重试调度：交给 Celery 框架，不标记失败
            raise
        except Exception:
            # F-30 修复：Celery retry() 重试耗尽时重新抛出原始异常而非
            # MaxRetriesExceededError，旧代码捕获不到导致笔记永久停留在
            # cleaning 状态。进入此分支即表示重试次数用尽，标记失败状态。
            try:
                asyncio.run(_update_note_status(
                    note_id, NoteStatus.cleaning_failed,
                    error_message=f"清洗任务重试失败: {str(exc)}",
                ))
            except Exception as update_err:
                logger.error(f"更新笔记状态失败 (note_id={note_id}): {update_err}")
