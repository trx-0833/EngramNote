"""
文档转换异步任务模块

本模块定义了 Celery 异步任务，负责将上传的文档转换为 Markdown 格式。
根据文件类型调用不同的转换引擎：
- PDF/图片/Office 文档 → 调用 mineru 子包进行文档解析
- 音视频 → 调用 asr 子包进行语音转写

主要职责：
- 定义 Celery 任务 convert_document_task
- 管理独立的数据库连接（Celery worker 运行在独立进程中）
- 执行文档转换核心逻辑（下载 → 转换 → 上传结果）
- 更新笔记状态和元数据
- 处理转换失败和重试

设计决策：
- Celery worker 运行在独立进程中，无法共享 FastAPI 的数据库连接，
  因此需要创建独立的数据库引擎和会话工厂
- 使用 asyncio.run() 在同步的 Celery 任务中运行异步代码
- 任务失败时自动重试（最多 2 次，间隔 60 秒）
- 重试次数用尽后标记笔记为 failed，记录错误信息
- 转换完成后将 Markdown 上传到对象存储，数据库中仅保存路径引用
"""

from typing import Optional

import os
import tempfile
import time
import traceback
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from .celery_app import celery_app
from ..config import get_settings
from ..models.note import Note, NoteStatus, SourceType
from ..services import vault_path
from ..services.vault_meta import write_note_meta

settings = get_settings()
logger = logging.getLogger(__name__)

# _update_note_status 允许更新的字段白名单（C-3: 防止任意字段写入）
_UPDATABLE_NOTE_FIELDS = {"title", "page_count", "original_md_path", "clean_md_path", "metadata_"}

# Celery 任务中需要独立的数据库连接（不在 FastAPI 上下文中）
# 这些全局变量在首次使用时延迟初始化
_sync_engine = None
_sync_session_factory = None


def _get_sync_session():
    """
    获取同步数据库会话工厂（Celery worker 中使用）

    Celery worker 运行在独立进程中，无法使用 FastAPI 的数据库连接池，
    因此需要创建独立的异步引擎和会话工厂。
    使用全局变量缓存，避免重复创建。

    Returns:
        async_sessionmaker: 异步会话工厂
    """
    global _sync_engine, _sync_session_factory
    if _sync_session_factory is None:
        # Celery 任务运行在独立进程中，需要自己的数据库连接
        _sync_engine = create_async_engine(settings.get_database_url(), echo=False)
        _sync_session_factory = async_sessionmaker(_sync_engine, expire_on_commit=False)
    return _sync_session_factory


async def _update_note_status(note_id: str, status: NoteStatus, error_message: Optional[str] = None, **kwargs):
    """
    更新笔记状态

    在 Celery worker 的独立数据库会话中更新笔记的状态和附加字段。
    支持通过 kwargs 动态更新任意笔记字段（如 page_count、metadata_ 等）。

    Args:
        note_id: 笔记 ID
        status: 新的状态
        error_message: 错误信息（可选，失败时使用）
        **kwargs: 其他需要更新的字段（如 page_count、original_md_path 等）
    """
    session_factory = _get_sync_session()
    async with session_factory() as session:
        result = await session.execute(select(Note).where(Note.id == note_id))
        note = result.scalars().first()
        if not note:
            return

        note.status = status
        if error_message:
            note.error_message = error_message
        # 动态更新附加字段（仅允许白名单中的字段）
        for key, value in kwargs.items():
            if key in _UPDATABLE_NOTE_FIELDS:
                setattr(note, key, value)
        await session.commit()
        await session.refresh(note)
        # 状态写穿镜像：同步更新 Vault output/meta/{base}.json
        write_note_meta(note)


async def _record_clean_task_id(note_id: str, task_id: str) -> None:
    """
    记录清洗任务 ID 到笔记元数据

    转换成功后自动触发清洗任务时，将 Celery 任务 ID 写入笔记
    metadata_["clean_task_id"]，便于用户停止清洗时撤销任务。
    仅更新 metadata_，不修改笔记状态（_update_note_status 会无条件
    设置 status，不适合在此场景复用）。

    Args:
        note_id: 笔记 ID
        task_id: 清洗任务 ID
    """
    session_factory = _get_sync_session()
    async with session_factory() as session:
        result = await session.execute(select(Note).where(Note.id == note_id))
        note = result.scalars().first()
        if not note:
            return
        note.metadata_ = dict(note.metadata_ or {})
        note.metadata_["clean_task_id"] = task_id
        await session.commit()
        await session.refresh(note)
        # 元数据写穿镜像：同步更新 Vault output/meta/{base}.json
        write_note_meta(note)


async def _convert_document(note_id: str, file_path: str, source_type: str, backend: Optional[str] = None):
    """
    执行文档转换的核心逻辑

    完整流程：
    1. 从对象存储下载原始文件到临时目录
    2. 根据文件类型选择转换引擎：
       a. 文档类（PDF/图片/Office）→ 调用 mineru 子包转换为 Markdown
       b. 音视频 → 调用 asr 子包完成转写、标点恢复和标题生成
    3. 将转换结果（Markdown）上传到对象存储
    4. 更新笔记状态和元数据

    Args:
        note_id: 笔记 ID
        file_path: 原始文件在对象存储中的路径
        source_type: 文件来源类型（pdf/image/docx/pptx/xlsx/audio/video）
    """
    start_time = time.monotonic()
    logger.info("文档转换开始: note_id=%s, source_type=%s", note_id, source_type)

    # 检查笔记是否已被用户删除（状态为 failed 且 error_message 为 "用户手动删除"）
    session_factory = _get_sync_session()
    async with session_factory() as session:
        note = await session.get(Note, note_id)
        if not note or (note.status == NoteStatus.failed and note.error_message == "用户手动删除"):
            logger.info(f"笔记 {note_id} 已被用户删除，跳过转换任务")
            return
    from ..services.storage_service import (
        download_file,
        upload_bytes,
        ensure_buckets_exist,
    )

    # 确保存储桶目录存在
    ensure_buckets_exist()

    # 1. 从 MinIO 下载原始文件到临时目录
    with tempfile.TemporaryDirectory(prefix="engram_convert_") as tmp_dir:
        # 保留原始文件扩展名，部分转换工具依赖扩展名判断文件类型
        ext = os.path.splitext(file_path)[1] if "." in file_path else ""
        local_file = os.path.join(tmp_dir, f"input{ext}")
        download_file(settings.minio_bucket_original, file_path, local_file)

        source_type_enum = SourceType(source_type)
        markdown_content = ""
        metadata = {}

        if source_type_enum in (SourceType.pdf, SourceType.image, SourceType.docx, SourceType.pptx, SourceType.xlsx):
            # 2a. 文档类 → 调用 mineru 子包转换
            try:
                from ..services.mineru.converter import convert
                result = convert(
                    local_file,
                    backend=backend or settings.mineru_backend,
                    lang="ch",
                    formula_enable=True,
                    table_enable=True,
                    api_token=settings.mineru_api_token,
                    server_url=settings.mineru_server_url,
                )

                if result.success:
                    markdown_content = result.markdown_content
                    metadata = result.metadata or {}
                    # 转换成功，更新状态为 converted 并保存页数等元数据
                    await _update_note_status(
                        note_id, NoteStatus.converted,
                        page_count=metadata.get("page_count"),
                        metadata_=metadata,
                    )
                else:
                    # mineru 返回失败结果
                    elapsed = time.monotonic() - start_time
                    logger.error("Mineru 转换失败: note_id=%s, source_type=%s, elapsed=%.1fs", note_id, source_type, elapsed)
                    await _update_note_status(
                        note_id, NoteStatus.failed,
                        error_message=f"Mineru 转换失败: {result.error}",
                    )
                    return
            except Exception as e:
                # mineru 调用异常（如模块未安装、文件损坏等）
                elapsed = time.monotonic() - start_time
                logger.error("Mineru 调用异常: note_id=%s, source_type=%s, elapsed=%.1fs", note_id, source_type, elapsed)
                await _update_note_status(
                    note_id, NoteStatus.failed,
                    error_message=f"Mineru 调用异常: {traceback.format_exc()}",
                )
                return

        elif source_type_enum == SourceType.markdown:
            # 2c. Markdown 文件 → 无需转换，直接读取内容
            try:
                with open(local_file, "r", encoding="utf-8") as f:
                    markdown_content = f.read()
                await _update_note_status(
                    note_id, NoteStatus.converted,
                )
            except Exception as e:
                elapsed = time.monotonic() - start_time
                logger.error("Markdown 读取失败: note_id=%s, elapsed=%.1fs", note_id, elapsed)
                await _update_note_status(
                    note_id, NoteStatus.failed,
                    error_message=f"Markdown 文件读取失败: {traceback.format_exc()}",
                )
                return

        elif source_type_enum in (SourceType.audio, SourceType.video):
            # 2b. 音视频 → 调用 asr 子包转写
            try:
                from ..services.asr import convert as asr_convert

                result = asr_convert(
                    local_file,
                    language=settings.asr_language or None,
                    use_cache=True,
                    enable_punctuation=settings.asr_enable_punctuation,
                    enable_title_generation=settings.asr_enable_title_generation,
                    cache_dir=settings.asr_cache_dir or None,
                )

                if result.success:
                    markdown_content = result.markdown_content
                    metadata = result.metadata or {}

                    # 将 ASR 生成的标题更新到笔记
                    if result.title and result.title != "未命名":
                        await _update_note_status(
                            note_id, NoteStatus.converted,
                            title=result.title,
                            metadata_=metadata,
                        )
                    else:
                        await _update_note_status(
                            note_id, NoteStatus.converted,
                            metadata_=metadata,
                        )
                else:
                    elapsed = time.monotonic() - start_time
                    logger.error("ASR 转写失败: note_id=%s, source_type=%s, elapsed=%.1fs", note_id, source_type, elapsed)
                    await _update_note_status(
                        note_id, NoteStatus.failed,
                        error_message=f"ASR 转写失败: {result.error}",
                    )
                    return
            except Exception as e:
                elapsed = time.monotonic() - start_time
                logger.error("ASR 转写异常: note_id=%s, source_type=%s, elapsed=%.1fs", note_id, source_type, elapsed)
                await _update_note_status(
                    note_id, NoteStatus.failed,
                    error_message=f"ASR 转写异常: {traceback.format_exc()}",
                )
                return

        # 3. 将 Markdown 存入 MinIO
        if markdown_content:
            # Vault 命名关联法则：{user_id}/{project_slug}/output/markdown/{base}.md
            # 与 source/{base}{ext} 同主干，仅扩展名不同
            parts = file_path.split("/")
            prefix = "/".join(parts[:2])
            base = os.path.splitext(parts[-1])[0]
            md_object_name = vault_path.markdown_object(prefix, base)
            upload_bytes(
                settings.minio_bucket_markdown,
                md_object_name,
                markdown_content.encode("utf-8"),
                content_type="text/markdown; charset=utf-8",
            )
            # 更新笔记的 Markdown 文件路径
            await _update_note_status(
                note_id, NoteStatus.converted,
                original_md_path=md_object_name,
            )

            # 转换成功后自动触发清洗任务
            try:
                from .clean_tasks import clean_document_task
                task = clean_document_task.delay(note_id)
                # 记录清洗任务 ID，便于用户停止清洗时撤销任务
                await _record_clean_task_id(note_id, task.id)
            except Exception:
                # 清洗任务触发失败不影响转换结果
                pass

    elapsed = time.monotonic() - start_time
    logger.info("文档转换完成: note_id=%s, source_type=%s, elapsed=%.1fs", note_id, source_type, elapsed)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=60)
def convert_document_task(self, note_id: str, file_path: str, source_type: str, backend: Optional[str] = None):
    """
    Celery 任务：文档转换

    作为 Celery 异步任务执行，由 upload API 触发。
    使用 asyncio.run() 在同步的 Celery 任务中运行异步的转换逻辑。

    任务配置：
    - bind=True：可访问 self（任务实例），用于重试
    - max_retries=2：最多重试 2 次
    - default_retry_delay=60：重试间隔 60 秒

    Args:
        self: Celery 任务实例（bind=True 时自动传入）
        note_id: 笔记 ID
        file_path: 原始文件在对象存储中的路径
        source_type: 文件来源类型
        backend: 解析后端选择（可选），如 "pipeline"（本地）或 "vlm-http-client"（云端），
                 为 None 时使用 config.py 中的 mineru_backend 默认值
    """
    import asyncio

    try:
        asyncio.run(_convert_document(note_id, file_path, source_type, backend))
    except Exception as exc:
        logger.error("转换任务异常: note_id=%s, source_type=%s, error=%s", note_id, source_type, exc)
        # 转换失败，尝试重试
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            # 重试次数用尽，标记笔记为失败状态
            asyncio.run(_update_note_status(
                note_id, NoteStatus.failed,
                error_message=f"转换任务重试失败: {str(exc)}",
            ))
