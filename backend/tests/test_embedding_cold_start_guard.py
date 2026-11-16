"""round5 第 2 条：冷启动降级护栏**零守护** —— 删掉护栏，12 条用例仍全绿。

这里补两条：编码**前**拿到的是配置名、编码**后**才发现降级 ⇒ 必须
「丢弃本轮结果、不写库」，并且返回 `skipped_reason='fallback_model'`。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models.chunk import Chunk
from app.models.knowledge_card import KnowledgeCard
from app.models.note import Note, NoteStatus, SourceType
from app.models.user import User

_UID = "user-cold-1"
_VEC = [0.1, 0.2, 0.3, 0.4]


async def _seed(db, *, chunk: bool) -> None:
    db.add(User(id=_UID, email="c@t.local", username="cold", hashed_password="x", is_active=True))
    await db.flush()
    db.add(
        Note(
            id="n-cold",
            user_id=_UID,
            title="冷启动",
            source_type=SourceType.pdf,
            status=NoteStatus.cleaned,
            file_size=1,
            original_file_path="/n.pdf",
            original_md_path="/n.md",
        )
    )
    await db.flush()
    if chunk:
        db.add(
            Chunk(
                id="c-cold",
                user_id=_UID,
                note_id="n-cold",
                index=0,
                content="待补内容",
                char_start=0,
                char_end=4,
                char_count=4,
                has_embedding=False,
            )
        )
    else:
        db.add(
            KnowledgeCard(
                id="card-cold",
                user_id=_UID,
                note_id="n-cold",
                card_type="concept",
                title="卡片",
                content="待补内容",
                has_embedding=False,
            )
        )
    await db.commit()


def _two_stage_model(monkeypatch, *, et, configured: str, degraded: str):
    """第一次 `_model_name()` 返回配置名（模型还没加载），之后返回降级名。"""
    state = {"n": 0}

    def fake_model_name():
        state["n"] += 1
        return configured if state["n"] == 1 else degraded

    monkeypatch.setattr(et, "_model_name", fake_model_name)
    return state


@pytest.mark.asyncio
class TestColdStartGuard:
    async def test_chunk_result_is_discarded_when_model_turns_out_degraded(self, test_db, monkeypatch):
        from app.tasks import embedding_tasks as et

        configured = get_settings().embedding_model
        degraded = "BAAI/bge-small-zh-v1.5" if configured != "BAAI/bge-small-zh-v1.5" else "BAAI/bge-m3"

        async with test_db() as db:
            await _seed(db, chunk=True)

        _two_stage_model(monkeypatch, et=et, configured=configured, degraded=degraded)
        monkeypatch.setattr(et, "get_sync_session", lambda: test_db) if hasattr(et, "get_sync_session") else None
        monkeypatch.setattr(et, "encode_text", lambda texts: [list(_VEC) for _ in texts], raising=False)

        result = await et._embed_chunks_async(limit=10)
        assert result.get("skipped_reason") == "fallback_model", f"编码后才确认降级时应当丢弃本轮，实际 {result}"

        async with test_db() as db:
            row = (await db.execute(select(Chunk).where(Chunk.id == "c-cold"))).scalars().one()
            assert row.has_embedding is False, "降级维度被写进库了（永久错标）"
            assert row.embedding is None

    async def test_card_result_is_discarded_when_model_turns_out_degraded(self, test_db, monkeypatch):
        from app.tasks import embedding_tasks as et

        configured = get_settings().embedding_model
        degraded = "BAAI/bge-small-zh-v1.5" if configured != "BAAI/bge-small-zh-v1.5" else "BAAI/bge-m3"

        async with test_db() as db:
            await _seed(db, chunk=False)

        _two_stage_model(monkeypatch, et=et, configured=configured, degraded=degraded)
        monkeypatch.setattr(et, "encode_text", lambda texts: [list(_VEC) for _ in texts], raising=False)

        result = await et._embed_cards_async(_UID, 10, lambda _t, _c: "文本")
        assert result.get("skipped_reason") == "fallback_model", f"卡片侧编码后才确认降级时应当丢弃本轮，实际 {result}"

        async with test_db() as db:
            row = (await db.execute(select(KnowledgeCard).where(KnowledgeCard.id == "card-cold"))).scalars().one()
            assert row.has_embedding is False, "降级维度被写进卡片表了（永久错标）"
