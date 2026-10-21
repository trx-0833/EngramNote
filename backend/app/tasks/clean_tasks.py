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


import os
import time
import logging

from celery.exceptions import Retry

from sqlalchemy import select

from .celery_app import celery_app
from .common import (
    get_sync_session as _get_clean_session,
    update_note_status as _update_note_status,
    get_note_status as _get_note_status,
    # 任务追踪（阶段 1′ 1.7）
    begin_task_run as _begin_task_run,
    mark_task_succeeded as _mark_task_succeeded,
    mark_task_failed as _mark_task_failed,
    mark_task_cancelled as _mark_task_cancelled,
    report_task_progress as _report_progress,
    clear_progress_cache as _clear_progress_cache,
    task_id_of as _task_id_of,
)
from .loop import run_async as _run_async, task_loop as _task_loop
from ..config import get_settings
from ..models.note import Note, NoteStatus
from ..services import vault_path

settings = get_settings()
logger = logging.getLogger(__name__)


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
        find_duplicates_by_embedding,
        find_duplicates_lightweight,
        generate_clean_copy,
    )
    from ..services.markdown_segmenter import segment_with_offsets, to_retrieval_chunks
    from ..services.embedding_service import (
        EmbeddingService,
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

    # 4. 文本分块（阶段 2.1：统一走 markdown_segmenter，它同时产出
    #    字符偏移与标题路径，供阶段 2.7 的引用回跳使用）
    segments = segment_with_offsets(cleaned_text, settings.chunk_size)
    chunks = to_retrieval_chunks(segments)
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
        # 6+7. 基于嵌入向量的块级去重
        #
        # 阶段 2.4 收尾：原先这里把向量写进 Chroma，再由
        # `find_duplicates` 把**全部向量读回来**在 Python 里两两比较 ——
        # 整趟 Chroma 往返只是为了"存起来又取回来"，并没有用到向量库的
        # 检索能力（不是近邻查询，就是两个嵌套 for）。
        #
        # 改为直接消费刚算好的 `embeddings`：数学完全相同
        # （同一个 compute_similarity、同样的两两循环与 overlap 跳过规则），
        # 但省掉一次写库 + 一次读库，也让清洗流程不再依赖 Chroma。
        try:
            duplicates = find_duplicates_by_embedding(chunks, embeddings)
        except Exception as e:
            # 去重失败不影响清洗，降级文本去重后继续生成副本
            logger.warning(
                "向量去重失败，降级为文本去重: note_id=%s, err=%s", note_id, e
            )
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

    # 7c. **段落级**去重（B1 第 ④ 项，2026-10-22）
    #
    # ## 为什么块级去重不够
    #
    # 检索分块是 `chunk_size=500`（`config.py`），于是一段 100 字的重复文字
    # 会和它前后文**落在同一个 500 字块里** —— 块与块的相似度因此远低于阈值，
    # 检测不到。走查报告 §5 实测：样本里刻意重复的段落**仍出现 2 次**，
    # 而界面上显示「本次未检测到重复块」。
    #
    # **那是"静默说谎"**：用户看到的是"没有重复"，事实是"我们按 500 字找的"。
    # 两个修法各做一半 —— 检测补到段落级（这里），文案如实描述粒度（前端）。
    #
    # ## ⚠️ 两套 `block_index` **不是同一个坐标系**（第一版在这里搞错了）
    #
    # 块级的 `block_index` 按**检索块**（500 字）编号，段落级按**段落**（空行分界）
    # 编号。所以它们**不能混进同一次 `generate_clean_copy` 调用** ——
    # 那个函数的 `block_index` 是拿来查行范围的，坐标错了就会把标记打在
    # **错误的行**上，而且不会报错（只是标错）。
    #
    # 第一版想"跑两次、把结果拼起来"，那也不行：第二次调用会从 `cleaned_text`
    # 重新分块并重新输出，把第一次的标记**整片覆盖掉**。
    #
    # 正解是**按行范围标记**，而不是按块索引：段落级的结果自带
    # `start_line` / `end_line`，用 `apply_line_range_marks()` 在块级标记之后
    # **追加**注释 —— 两套坐标因此不需要互相翻译。
    paragraph_duplicates: list = []
    try:
        from ..services.cleaning_service import (
            apply_line_range_marks,
            find_duplicate_paragraphs,
        )

        paragraph_duplicates = find_duplicate_paragraphs(cleaned_text, duplicates)
    except Exception as e:  # noqa: BLE001 - 段落级去重失败不该让清洗挂掉
        logger.warning("段落级去重失败（块级结果照常使用）: note_id=%s, err=%s", note_id, e)

    if paragraph_duplicates:
        logger.info(
            "段落级去重补充: note_id=%s, 新增 %d 条（块级 %d 条）",
            note_id, len(paragraph_duplicates), len(duplicates),
        )

    # 8. 生成清洗副本
    #
    # `total_blocks` 显式传入：`chunks` 就是下面 metadata 里 `total_chunks` 的来源，
    # 同一个数字传给两边，`copy_stats.total_blocks` 与 `total_chunks` 就不可能打架
    # （2026-10-21 修：此前不传，且函数内部只在"有重复块"时才计算，
    #   导致无重复块的文档 total_blocks 恒为 0）
    #
    # ⚠️ 粒度：这一趟的 `duplicates` 是**块级**检出来的，所以默认
    # `granularity="chunk"`（`block_index` 按检索块坐标）。段落级那批
    # **不能混进同一次调用** —— 两套索引不是同一个坐标系，混着传会让标记
    # 打在错误的行上。所以段落级的标记单独走一次（下面 8b）。
    clean_copy, copy_stats = generate_clean_copy(
        original_text, cleaned_text, duplicates, total_blocks=len(chunks)
    )

    # 8b. 段落级的重复标记：按**行范围**追加（与块级标记并存，互不覆盖）
    para_marked = 0
    if paragraph_duplicates:
        clean_copy, para_marked = apply_line_range_marks(clean_copy, paragraph_duplicates)
        copy_stats["duplicate_blocks_marked"] += para_marked
        # 粒度如实记为"两种都跑了"：界面据此说清"按段落 + 按 500 字块都找过"
        copy_stats["granularity"] = "chunk+paragraph"
    copy_stats["paragraph_duplicates_marked"] = para_marked

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
            # 记录重复块的精确行区间，供恢复/删除按行定位（见 docs/decisions.md#F-11）
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

    # 11. 建 chunk 索引（阶段 2.2′ / 2.3）
    #
    # 为什么放在清洗流程里而不是留给手动脚本：`chunks` 是**派生数据**，
    # 派生数据最常见的失效方式是"原文变了、索引没重建"（A-18 那一类）。
    # 手动入口意味着新笔记清洗后不会进索引，直到有人记得跑脚本 ——
    # 用户看到的是"新上传的资料问不出来"且不知原因。
    #
    # 失败**不抛异常**：清洗的主产物是 clean.md 与状态；索引缺失时
    # 检索退化为只用 BM25 通道，属于可用状态，不该让整个清洗任务失败。
    #
    # ── 2026-10-21 改（D2-③）──
    #
    # 原文案是 `logger.warning("chunk 索引重建失败（不影响清洗结果）: …")`。
    # **"不影响清洗结果"这半句把后果说小了**：索引重建失败意味着该笔记在
    # **FTS 与向量两条通道里都没有语料** —— 它不只是"少一层优化"，
    # 而是这篇笔记**问答/原文回跳都查不到**（走查报告 §5 第 7 条登记了这条，
    # 并指出"后果被低估"）。实测触发过一次：`Instance <Chunk …> has a NULL
    # identity key`（chunk_rowid 主键问题，已在 `f9f50ca` 修掉根因）。
    #
    # 现在两处一起改：
    #   ① **有界重试**（3 次，间隔 0.5/1/2 秒）—— 那次失败是事务/主键类的
    #      瞬时问题，重试一次就过去了；不重试等于让用户白等一次重新清洗；
    #   ② 仍然失败时**写进 `note.error_message`** —— 那个字段在界面上是可见的
    #      （`NoteDetailHeader.tsx:368`、`NotesList.tsx:373`、`CleaningPanel.tsx:258`），
    #      而日志只有运维看得到。走查报告要的"用户能知道"就是这一步。
    #
    # ⚠️ 重试**不**改变"失败不抛异常"这条：三次都失败也只是记一条可见的说明，
    # 清洗状态仍是 cleaned（主产物已经落盘）。
    _index_ok, _index_err = await _rebuild_chunk_index_with_retry(
        note_id=note_id,
        user_id=user_id,
        clean_md_path=clean_md_path,
        original_md_path=original_md_path,
    )
    if not _index_ok:
        await _mark_index_failure(note_id, _index_err)

    elapsed = time.monotonic() - start_time
    logger.info("文档清洗完成: note_id=%s, elapsed=%.1fs", note_id, elapsed)


#: chunk 索引重建的重试次数与退避（秒）
#:
#: 为什么是 3 次 / 0.5-1-2 秒：实测触发的那次失败（NULL identity key）是
#: 事务/主键类的**瞬时**问题，重试一次就过去了；再多的重试只会让清洗任务
#: 变长，而"笔记没有索引"这件事已经有可见出口（`error_message`）兜底。
_INDEX_RETRY_DELAYS = (0.5, 1.0, 2.0)


async def _rebuild_chunk_index_with_retry(
    *,
    note_id: str,
    user_id: str,
    clean_md_path: str,
    original_md_path: str,
) -> tuple[bool, str]:
    """重建 chunk 索引，失败时有界重试

    Returns:
        (是否成功, 失败原因)。成功时原因是空串。
    """
    import asyncio

    from ..services.chunk_service import index_note_from_storage

    last_err = ""
    for attempt, delay in enumerate((*_INDEX_RETRY_DELAYS, None), start=1):
        try:
            session_factory = _get_clean_session()
            async with session_factory() as chunk_db:
                n_chunks = await index_note_from_storage(
                    chunk_db,
                    note_id=note_id,
                    user_id=user_id,
                    clean_md_path=clean_md_path,
                    original_md_path=original_md_path,
                    chunk_size=settings.chunk_size,
                )
                await chunk_db.commit()
            logger.info("chunk 索引已重建: note_id=%s, chunks=%d（第 %d 次尝试）",
                        note_id, n_chunks, attempt)
            return True, ""
        except Exception as exc:  # noqa: BLE001 - 失败要记下来，不能让清洗任务挂
            last_err = f"{type(exc).__name__}: {exc}"
            if delay is None:
                break
            logger.warning(
                "chunk 索引重建失败（第 %d 次），%.1fs 后重试: note_id=%s, %s",
                attempt, delay, note_id, last_err,
            )
            await asyncio.sleep(delay)

    # ⚠️ 措辞刻意说清后果，不再写"不影响清洗结果"
    logger.error(
        "chunk 索引重建最终失败 —— 该笔记在检索通道里没有语料"
        "（问答与原文回跳都查不到它，不是'少一层优化'）: note_id=%s, %s",
        note_id, last_err,
    )
    return False, last_err


async def _mark_index_failure(note_id: str, reason: str) -> None:
    """把索引失败写进 `note.error_message`（界面上可见的那个字段）

    ⚠️ **不动 status**：清洗的主产物（clean.md）已经落盘，状态就该是 cleaned。
    这里只是补一条用户能看到的说明 —— 用 `update()` 而不是 `_update_note_status`
    正是为了避免连状态一起改。

    写入是**尽力而为**的：写不进去只记日志，不能让清洗任务失败
    （这条路径本来就是"出错了才走到"）。
    """
    from sqlalchemy import update

    from ..models.note import Note

    message = (
        f"检索索引未建好（{reason}）。这篇笔记的问答与原文定位暂时不可用，"
        f"重新清洗一次可重试。"
    )
    try:
        session_factory = _get_clean_session()
        async with session_factory() as db:
            await db.execute(
                update(Note).where(Note.id == note_id).values(error_message=message)
            )
            await db.commit()
        logger.info("已把索引失败写进 note.error_message: note_id=%s", note_id)
    except Exception as exc:  # noqa: BLE001 - 尽力而为
        logger.warning("写 note.error_message 失败（索引失败本身已记 error）: %s", exc)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=60)
def clean_document_task(self, note_id: str):
    """
    Celery 任务：文档清洗

    作为 Celery 异步任务执行，由转换完成或用户手动触发。

    阶段 4.5：整个任务体共用**一个**事件循环（见 `tasks/loop.py`），
    改造前这里有 7 次 `asyncio.run()`。

    任务配置：
    - bind=True：可访问 self（任务实例），用于重试
    - max_retries=2：最多重试 2 次
    - default_retry_delay=60：重试间隔 60 秒

    Args:
        self: Celery 任务实例（bind=True 时自动传入）
        note_id: 笔记 ID
    """
    task_id = _task_id_of(self)
    with _task_loop("clean_document"):
        if not _begin_task_run(self, note_id):
            return

        try:
            # 检查笔记是否已被用户停止清洗
            current_status = _run_async(_get_note_status(note_id))
            if current_status == NoteStatus.cleaning_failed:
                logger.info(f"笔记已被用户停止清洗，跳过任务 (note_id={note_id})")
                _mark_task_cancelled(self, "笔记已被用户停止清洗")
                return

            # 将状态更新为 cleaning
            # 手动触发时 API 层已预更新，此步为冗余但无害；
            # 自动触发（转换完成后）时 API 层未预更新，此步为必要
            try:
                _run_async(_update_note_status(note_id, NoteStatus.cleaning))
            except Exception:
                pass

            _run_async(_report_progress(task_id, 0.1, "正在清洗文本"))
            _run_async(_clean_document(note_id))
            _run_async(_report_progress(task_id, 0.95, "清洗完成"))
            _mark_task_succeeded(self)
        except Exception as exc:
            logger.error(f"清洗任务异常 (note_id={note_id}): {exc}", exc_info=True)

            # 重试前检查：如果用户已停止清洗，不再重试
            try:
                current_status = _run_async(_get_note_status(note_id))
                if current_status == NoteStatus.cleaning_failed:
                    logger.info(f"笔记已被用户停止清洗，放弃重试 (note_id={note_id})")
                    _mark_task_cancelled(self, "笔记已被用户停止清洗")
                    return
            except Exception:
                pass  # 状态查询失败时仍尝试重试

            try:
                self.retry(exc=exc)
            except Retry:
                # 正常重试调度：交给 Celery 框架，不标记失败
                raise
            except Exception:
                # Celery retry() 重试耗尽时重新抛出原始异常而非
                # MaxRetriesExceededError，旧代码捕获不到导致笔记永久停留在
                # cleaning 状态。进入此分支即表示重试次数用尽，标记失败状态（见 docs/decisions.md#F-30）。
                try:
                    _run_async(_update_note_status(
                        note_id, NoteStatus.cleaning_failed,
                        error_message=f"清洗任务重试失败: {str(exc)}",
                    ))
                except Exception as update_err:
                    logger.error(f"更新笔记状态失败 (note_id={note_id}): {update_err}")
                _mark_task_failed(self, f"清洗任务重试失败: {exc}")
        finally:
            _clear_progress_cache(task_id)
