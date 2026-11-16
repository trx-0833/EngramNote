"""M3 的门（round4）：`_pending_counts` / `_card_user_ids` 必须把"模型不符"算作待补。

审计指出这两处**零覆盖** —— 把判据改回 `has_embedding.is_(False)` 时 9 条用例全绿，
意味着"混存可自愈"这条承诺没有任何东西守着。
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.models.chunk import Chunk, pack_vector
from app.models.knowledge_card import KnowledgeCard
from app.models.note import Note, NoteStatus, SourceType
from app.models.user import User

_UID = "user-gate-1"
_VEC = [0.1, 0.2, 0.3, 0.4]


async def _seed(db, *, chunk_ok: bool, card_stale: bool) -> None:
    db.add(User(id=_UID, email="g@t.local", username="gate", hashed_password="x", is_active=True))
    await db.flush()
    db.add(
        Note(
            id="n-gate",
            user_id=_UID,
            title="门测试",
            source_type=SourceType.pdf,
            status=NoteStatus.cleaned,
            file_size=1,
            original_file_path="/n.pdf",
            original_md_path="/n.md",
        )
    )
    await db.flush()

    expected = get_settings().embedding_model
    stale = "BAAI/bge-small-zh-v1.5" if expected != "BAAI/bge-small-zh-v1.5" else "BAAI/bge-m3"

    db.add(
        Chunk(
            id="c-ok",
            user_id=_UID,
            note_id="n-gate",
            index=0,
            content="已用当前模型算过",
            char_start=0,
            char_end=8,
            char_count=8,
            has_embedding=True,
            embedding=pack_vector(_VEC),
            embedding_dim=len(_VEC),
            embedding_model=expected if chunk_ok else stale,
        )
    )
    db.add(
        KnowledgeCard(
            id="card-stale",
            user_id=_UID,
            note_id="n-gate",
            title="卡片",
            content="内容",
            card_type="concept",
            has_embedding=True,
            embedding=pack_vector(_VEC),
            embedding_dim=len(_VEC),
            embedding_model=stale if card_stale else expected,
        )
    )
    await db.commit()


@pytest.mark.asyncio
class TestPendingGates:
    async def test_chunk_with_wrong_model_counts_as_pending(self, test_db, monkeypatch):
        """模型不符的 chunk 必须算待补（否则 30 分钟一班的回填永远看不到它）"""
        from app.tasks import embedding_tasks as et

        async with test_db() as db:
            await _seed(db, chunk_ok=False, card_stale=False)

        # 用真正的 session factory（`_pending_counts` 内部会 import）
        import app.tasks.common as common

        monkeypatch.setattr(common, "get_sync_session", lambda: test_db)
        counts = await et._pending_counts()
        assert counts["chunks"] == 1, f"模型不符的 chunk 没被算作待补：{counts}"

    async def test_card_with_wrong_model_counts_as_pending(self, test_db, monkeypatch):
        """卡片侧同理 —— 这条是 round4 M3 指出的「门」"""
        from app.tasks import embedding_tasks as et

        async with test_db() as db:
            await _seed(db, chunk_ok=True, card_stale=True)

        counts = await et._pending_counts()
        assert counts["cards"] == 1, f"模型不符的卡片没被算作待补：{counts}"

        uids = await et._card_user_ids()
        assert _UID in uids, f"`_card_user_ids` 漏掉了有待补卡片的用户：{uids}"

    async def test_matching_model_is_not_pending(self, test_db, monkeypatch):
        """对照组：模型相符时不该报待补（防止断言恒真）"""
        from app.tasks import embedding_tasks as et

        async with test_db() as db:
            await _seed(db, chunk_ok=True, card_stale=False)

        counts = await et._pending_counts()
        assert counts["chunks"] == 0, f"模型相符却报待补：{counts}"
        assert counts["cards"] == 0, f"模型相符却报待补：{counts}"
