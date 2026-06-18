"""
文档转换异步任务模块

本模块定义了 Celery 异步任务，负责将上传的文档转换为 Markdown 格式。
根据文件类型调用不同的转换引擎：
- PDF/图片/Office 文档 → 调用 mineru_plus 进行文档解析
- 音视频 → 调用 asr_plus 进行语音转写

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

import sys
import os
import tempfile
import traceback

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from .celery_app import celery_app
from ..config import get_settings
from ..models.note import Note, NoteStatus, SourceType

settings = get_settings()

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
        # 动态更新附加字段
        for key, value in kwargs.items():
            if hasattr(note, key):
                setattr(note, key, value)
        await session.commit()


async def _convert_document(note_id: str, file_path: str, source_type: str, backend: Optional[str] = None):
    """
    执行文档转换的核心逻辑

    完整流程：
    1. 从对象存储下载原始文件到临时目录
    2. 根据文件类型选择转换引擎：
       a. 文档类（PDF/图片/Office）→ 调用 mineru_plus 转换为 Markdown
       b. 音视频 → 调用 asr_plus 转写为文本，再通过 DeepSeek 润色
    3. 将转换结果（Markdown）上传到对象存储
    4. 更新笔记状态和元数据

    Args:
        note_id: 笔记 ID
        file_path: 原始文件在对象存储中的路径
        source_type: 文件来源类型（pdf/image/docx/pptx/xlsx/audio/video）
    """
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
            # 2a. 文档类 → 调用 mineru_plus 转换
            try:
                # 将 mineru_plus 所在目录加入 sys.path，使其可被 import
                mineru_plus_path = os.path.join(os.path.dirname(settings.database_url), "..", "..", "mineru_plus")
                # 使用绝对路径定位项目根目录下的 mineru_plus 模块
                project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
                mineru_plus_path = os.path.join(project_root, "mineru_plus")
                if mineru_plus_path not in sys.path:
                    sys.path.insert(0, os.path.dirname(mineru_plus_path))

                from mineru_plus.converter import convert
                result = convert(
                    local_file,
                    backend=backend or settings.mineru_backend,
                    lang="ch",
                    formula_enable=True,
                    table_enable=True,)

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
                    # mineru_plus 返回失败结果
                    await _update_note_status(
                        note_id, NoteStatus.failed,
                        error_message=f"Mineru 转换失败: {result.error}",
                    )
                    return
            except Exception as e:
                # mineru_plus 调用异常（如模块未安装、文件损坏等）
                await _update_note_status(
                    note_id, NoteStatus.failed,
                    error_message=f"Mineru 调用异常: {traceback.format_exc()}",
                )
                return

        elif source_type_enum in (SourceType.audio, SourceType.video):
            # 2b. 音视频 → 调用 asr_plus 转写
            try:
                # 定位项目根目录下的 asr_plus 模块
                project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
                asr_plus_path = os.path.join(project_root, "asr_plus")
                if asr_plus_path not in sys.path:
                    sys.path.insert(0, os.path.dirname(asr_plus_path))

                from asr_plus.asr_engine import ASREngine, restore_punctuation, generate_title

                # ASR 语音转写
                engine = ASREngine.get_instance()
                transcribed_text, detected_lang = engine.transcribe_file(local_file, language="Chinese")

                # 使用 DeepSeek 对转写文本进行润色（添加标点、分段）
                if transcribed_text.strip():
                    polished_text = restore_punctuation(
                        transcribed_text,
                        api_key=settings.deepseek_api_key,
                        base_url=settings.deepseek_base_url,
                        model=settings.deepseek_model,
                    )
                    # 使用 DeepSeek 根据内容生成标题
                    title = generate_title(
                        transcribed_text,
                        api_key=settings.deepseek_api_key,
                        base_url=settings.deepseek_base_url,
                        model=settings.deepseek_model,
                    )
                else:
                    # 转写结果为空
                    polished_text = ""
                    title = "空转录"

                # 组装 Markdown 内容：标题 + 润色后的转写文本
                markdown_content = f"# {title}\n\n{polished_text}"
                metadata = {"detected_language": detected_lang}

                await _update_note_status(
                    note_id, NoteStatus.converted,
                    metadata_=metadata,
                )
            except Exception as e:
                # ASR 转写异常（如模型未加载、音频格式不支持等）
                await _update_note_status(
                    note_id, NoteStatus.failed,
                    error_message=f"ASR 转写异常: {traceback.format_exc()}",
                )
                return

        # 3. 将 Markdown 存入 MinIO
        if markdown_content:
            # Markdown 文件路径：与原始文件同目录，文件名为 original.md
            md_object_name = file_path.rsplit("/", 1)[0] + "/original.md"
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
                clean_document_task.delay(note_id)
            except Exception:
                # 清洗任务触发失败不影响转换结果
                pass


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
        # 转换失败，尝试重试
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            # 重试次数用尽，标记笔记为失败状态
            asyncio.run(_update_note_status(
                note_id, NoteStatus.failed,
                error_message=f"转换任务重试失败: {str(exc)}",
            ))
