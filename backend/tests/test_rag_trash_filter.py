"""审计 major 的回归用例：词法通道必须排除回收站笔记。

`_agenttmp/audit-report-rag.md` 实测：`_lexical_search` 调 `search_chunks_fts` 时
没传 `exclude_note_ids`，于是**已删除（回收站）的资料仍会进入上下文与引用列表**。
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import insert

from app.models.chunk import Chunk, pack_vector
from app.models.note import Note, NoteStatus, SourceType
from app.models.user import User

_FAKE_VEC = [1.0, 0.0, 0.0, 0.0]


async def _seed(db, *, note_id: str, trashed: bool, uid: str) -> None:
    await db.execute(
        insert(User).values(id=uid, email=f"{uid[:6]}@t.local", username=uid[:6], hashed_password="x", is_active=True)
    )
    await db.execute(
        insert(Note).values(
            id=note_id,
            user_id=uid,
            title=f"笔记 {note_id}",
            source_type=SourceType.pdf.value,
            status=NoteStatus.cleaned.value,
            file_size=1,
            original_file_path=f"/{note_id}.pdf",
            original_md_path=f"/{note_id}.md",
            # 关键：回收站标记
            trashed_at=datetime(2026, 11, 15, 10, 0, 0) if trashed else None,
        )
    )
    await db.execute(
        insert(Chunk).values(
            id=f"c-{note_id}",
            user_id=uid,
            note_id=note_id,
            index=0,
            content="水导轴承共有 12 块瓦",
            char_start=0,
            char_end=12,
            char_count=12,
            has_embedding=True,
            embedding=pack_vector(_FAKE_VEC),
            embedding_dim=len(_FAKE_VEC),
            embedding_model="test",
        )
    )
    await db.commit()


async def _seed_note_only(db, *, note_id: str, trashed: bool, uid: str) -> None:
    """只建笔记 + 一个 chunk（用户已存在）"""
    await db.execute(
        insert(Note).values(
            id=note_id,
            user_id=uid,
            title=f"笔记 {note_id}",
            source_type=SourceType.pdf.value,
            status=NoteStatus.cleaned.value,
            file_size=1,
            original_file_path=f"/{note_id}.pdf",
            original_md_path=f"/{note_id}.md",
            trashed_at=datetime(2026, 11, 15, 10, 0, 0) if trashed else None,
        )
    )
    await db.execute(
        insert(Chunk).values(
            id=f"c-{note_id}",
            user_id=uid,
            note_id=note_id,
            index=0,
            content="水导轴承共有 12 块瓦",
            char_start=0,
            char_end=12,
            char_count=12,
            has_embedding=True,
            embedding=pack_vector(_FAKE_VEC),
            embedding_dim=len(_FAKE_VEC),
            embedding_model="test",
        )
    )
    await db.commit()


@pytest.mark.asyncio
class TestLexicalExcludesTrash:
    async def test_trashed_note_is_not_in_lexical_hits(self, test_db, monkeypatch):
        from app.services.rag_service import RAGService

        uid = "user-trash-1"
        async with test_db() as db:
            # 用户只建一次（_seed 第二次会撞主键）
            await _seed(db, note_id="n-live", trashed=False, uid=uid)
        async with test_db() as db:
            await _seed_note_only(db, note_id="n-dead", trashed=True, uid=uid)

        service = RAGService()
        service._session_factory = test_db

        trashed = await service._trashed_note_ids(uid)
        assert trashed == {"n-dead"}, "回收站集合没取到"

        # ★ 契约级断言（2026-11-15 第二轮审计）：FTS 查询必须带上回收站集合。
        #
        # 为什么不再断言"结果里没有 n-dead"：测试库 `chunks_fts` 为空时，实现会从
        # `hits == []` 的逃生口返回，`return hits` 根本没执行 —— 那种断言恒真。
        # 这里改为记录实际参数，直接守"有没有把 exclude_note_ids 传下去"。
        captured: dict = {}

        async def _fake_search(_session, question, user_id, *, top_k=20, exclude_note_ids=None):
            captured["exclude_note_ids"] = exclude_note_ids
            captured["question"] = question
            return []

        from app.services import fts_search_service as _fts

        async def _fts_available(_session):
            return True

        monkeypatch.setattr(_fts, "fts5_available", _fts_available)
        monkeypatch.setattr(_fts, "search_chunks_fts", _fake_search)

        await service._lexical_search(uid, "水导轴承", [], top_k=10, exclude_note_ids=trashed)

        assert captured.get("exclude_note_ids") == {"n-dead"}, (
            f"FTS 查询没带回收站集合（实际 {captured.get('exclude_note_ids')!r}） —— 已删除笔记的正文会照样进上下文"
        )
