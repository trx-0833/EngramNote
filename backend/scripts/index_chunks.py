#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Chunk 索引脚本（overhaul-plan 阶段 2.2′ 的 A 半）
================================================

把每篇笔记的 Markdown 用统一分块器切成 chunk，连同**字符偏移**与
**标题路径**落进 `chunks` 表，使"检索单元"成为可引用的一等实体。

## 这一步解决什么

引用回跳（2.7）此前无处可跳：chunk 的位置信息在检索链路上被逐层丢弃
（附录 N.4 记录了四层里丢三层）。落库之后，回跳变成
**按 chunk_id 查偏移**，不再依赖 Chroma 的元数据能否穿过每一层。

同时它也是 2.3（语料切换）的基础：有了 chunk 表，
"检索命中哪一段原文"才有稳定的指称。

## 本轮**不写向量**（A 半）

`embedding` 列留空，`has_embedding=0`。原因：重新嵌入 1872 个 chunk 要加载
`bge-m3`（4356MB），而本机可用内存 4.51GB、门槛 4.0GB，余量过低，
中途失败会留下半新半旧的索引（附录 N.5）。B 半单独执行。

## 幂等性

按 `(note_id, index)` 唯一约束 upsert：

- 默认**全量重建**该笔记的 chunk（先删后插，同一事务）
- `--only-stale` 时跳过 `content_hash` 与当前分块结果一致的笔记

`content_hash` 覆盖 chunk 内容与来源对象名 —— 原文改了而索引没重建，
是这类派生表最典型的失效方式（A-18 就是"编辑笔记后向量永久陈旧"）。

## 校验

`--verify` 重新读取落库内容并断言核心不变量：

    text[char_start:char_end] == content

这条不变量在开发过程中被违反过**两次**（各 1318 个分段，见附录 L.2），
两次都是靠"跑遍全部真实文件"发现的。落库链路同样要能自证。

用法：
    python scripts/index_chunks.py --dry-run         # 只看会发生什么
    python scripts/index_chunks.py                   # 落库
    python scripts/index_chunks.py --verify          # 校验不变量与覆盖率
    python scripts/index_chunks.py --only-stale      # 只重建原文变了的笔记
    python scripts/index_chunks.py --user <user_id>  # 只处理某用户
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def _log(msg: str) -> None:
    print(msg, flush=True)


async def _load_markdown(note) -> tuple[str | None, str | None]:
    """读取笔记的 Markdown（优先 clean_md_path），返回 (文本, 对象名)"""
    from app.config import get_settings
    from app.services.storage_service import get_object_bytes

    settings = get_settings()
    for path in (note.clean_md_path, note.original_md_path):
        if not path:
            continue
        try:
            data = get_object_bytes(settings.minio_bucket_markdown, path)
            return data.decode("utf-8"), path
        except Exception:
            continue
    return None, None


def _chunk_hash(chunks: list[dict], source_path: str | None) -> str:
    """整篇的分块指纹（内容 + 来源对象名）

    覆盖来源对象名是必要的：`clean_md_path` 从 original 换成 clean 副本时
    内容可能碰巧相同，但"这段来自哪个文件"变了，偏移的参照物也就变了。
    """
    digest = hashlib.sha256()
    digest.update((source_path or "").encode())
    for c in chunks:
        digest.update(c["content"].encode())
        digest.update(f"{c['char_start']}:{c['char_end']}".encode())
    return digest.hexdigest()


async def run(args) -> int:
    from sqlalchemy import delete, func, select

    from app import database as db_mod
    from app.config import get_settings
    from app.models.chunk import Chunk
    from app.models.note import Note
    from app.services.markdown_segmenter import segment_with_offsets, to_retrieval_chunks

    settings = get_settings()
    await db_mod.init_db()
    session_factory = db_mod.get_session_factory()

    # ---- 1. 选出要处理的笔记 ----
    async with session_factory() as db:
        query = select(Note).where(Note.trashed_at.is_(None))
        if args.user:
            query = query.where(Note.user_id == args.user)
        notes = list((await db.execute(query)).scalars().all())

    _log(f"待处理笔记: {len(notes)} 篇   chunk_size={settings.chunk_size}")

    total_chunks = 0
    written_notes = 0
    skipped_missing = 0
    skipped_fresh = 0
    invariant_bad = 0

    for note in notes:
        text, source_path = await _load_markdown(note)
        if text is None:
            skipped_missing += 1
            _log(f"  [跳过] 无法读取 Markdown: {note.title[:40]!r} "
                 f"(clean={bool(note.clean_md_path)} orig={bool(note.original_md_path)})")
            continue

        segments = segment_with_offsets(text, settings.chunk_size)
        chunks = to_retrieval_chunks(segments)

        # 落库前先自证不变量（错误偏移比缺失更糟，不能写进去）
        for c in chunks:
            if text[c["char_start"]:c["char_end"]] != c["content"]:
                invariant_bad += 1
        if invariant_bad:
            _log(f"  [中止] {note.title[:40]!r} 的分块违反不变量 "
                 f"({invariant_bad} 处) —— 拒绝落库错误偏移")
            return 2

        fingerprint = _chunk_hash(chunks, source_path)

        async with session_factory() as db:
            if args.only_stale:
                existing = (await db.execute(
                    select(Chunk.content_hash).where(
                        Chunk.note_id == note.id, Chunk.index == 0
                    )
                )).scalars().first()
                have = (await db.execute(
                    select(func.count()).select_from(Chunk).where(Chunk.note_id == note.id)
                )).scalar() or 0
                if existing == fingerprint and have == len(chunks):
                    skipped_fresh += 1
                    continue

            if args.dry_run:
                _log(f"  [dry-run] {note.title[:40]!r}: {len(chunks)} 个 chunk, "
                     f"来源={source_path}")
                total_chunks += len(chunks)
                written_notes += 1
                continue

            # 先删后插（同一事务）：保证幂等，且不会留下上一轮的陈旧行
            await db.execute(delete(Chunk).where(Chunk.note_id == note.id))
            db.add_all([
                Chunk(
                    user_id=note.user_id,
                    note_id=note.id,
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
            await db.commit()

        total_chunks += len(chunks)
        written_notes += 1

    _log("")
    _log(f"完成: 写入 {written_notes} 篇 / {total_chunks} 个 chunk")
    _log(f"  跳过（Markdown 读不到）: {skipped_missing}")
    if args.only_stale:
        _log(f"  跳过（内容未变）: {skipped_fresh}")
    if args.dry_run:
        _log("  （dry-run：未写入任何数据）")

    # ---- 2. 落库后自检 ----
    async with session_factory() as db:
        rows = (await db.execute(
            select(func.count()).select_from(Chunk)
        )).scalar() or 0
        embedded = (await db.execute(
            select(func.count()).select_from(Chunk).where(Chunk.has_embedding.is_(True))
        )).scalar() or 0
    _log(f"chunks 表现有行数: {rows}（已嵌入 {embedded}，本轮的 A 半不写向量）")
    return 0


async def verify(args) -> int:
    """校验落库内容的正确性：不变量 + 覆盖率 + 与原文一致"""
    from sqlalchemy import select

    from app import database as db_mod
    from app.models.chunk import Chunk
    from app.models.note import Note

    await db_mod.init_db()
    session_factory = db_mod.get_session_factory()

    problems: list[str] = []
    notes_checked = 0
    chunks_checked = 0

    async with session_factory() as db:
        note_ids = list((await db.execute(
            select(Chunk.note_id).group_by(Chunk.note_id)
        )).scalars().all())

    _log(f"校验 {len(note_ids)} 篇笔记的 chunk …")

    for note_id in note_ids:
        async with session_factory() as db:
            note = (await db.execute(
                select(Note).where(Note.id == note_id)
            )).scalars().first()
            chunks = list((await db.execute(
                select(Chunk).where(Chunk.note_id == note_id).order_by(Chunk.index)
            )).scalars().all())

        if note is None:
            problems.append(f"{note_id}: 笔记已不存在但 chunk 仍在（孤儿行）")
            continue

        text, _src = await _load_markdown(note)
        if text is None:
            problems.append(f"{note_id}: 无法读取 Markdown，无法校验")
            continue

        notes_checked += 1
        covered = 0
        for c in chunks:
            chunks_checked += 1
            # 核心不变量
            if text[c.char_start:c.char_end] != c.content:
                problems.append(
                    f"{note_id}#{c.index}: 不变量被破坏 "
                    f"(chars {c.char_start}-{c.char_end})"
                )
            if c.char_count != len(c.content):
                problems.append(f"{note_id}#{c.index}: char_count 不符")
            if c.line_start > c.line_end:
                problems.append(f"{note_id}#{c.index}: 行号区间倒置")
            # 与落库的 content_hash 一致性（防止手工改过内容）
            covered += c.char_end - c.char_start

        # 覆盖率：分段必须覆盖原文（丢失内容**不违反**不变量，见附录 L.3）
        ratio = covered / len(text) if text else 1.0
        if ratio < 0.98:
            problems.append(f"{note_id}: 覆盖率仅 {ratio:.0%}（有内容未落库）")

    _log("")
    _log(f"校验完成: {notes_checked} 篇 / {chunks_checked} 个 chunk")
    if problems:
        _log(f"发现 {len(problems)} 处问题（前 10）:")
        for p in problems[:10]:
            _log(f"  - {p}")
        return 1
    _log("不变量与覆盖率全部通过。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Chunk 索引（阶段 2.2′ A 半）")
    ap.add_argument("--user", default="", help="只处理该用户的笔记")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写库")
    ap.add_argument("--only-stale", action="store_true",
                    help="跳过 content_hash 未变的笔记")
    ap.add_argument("--verify", action="store_true",
                    help="只校验已落库内容，不做索引")
    args = ap.parse_args()

    if args.verify:
        return asyncio.run(verify(args))
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
