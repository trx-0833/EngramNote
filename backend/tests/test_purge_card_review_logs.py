"""
物理删除笔记：卡片级复习记录的外键（2026-11-08 真机复现）
==========================================================

## 现场

真实环境冒烟（测试账号 + 真 PDF + MinerU + 理解出卡）走到最后一步：

    POST /api/review/cards/{card_id}/submit     → 200
    DELETE /api/notes/{id}/purge                → 500 INTERNAL_ERROR

`errors.log` 里的 traceback：

    File "app/services/note_service.py", line 612, in purge_note
      await db.execute(sql_delete(KnowledgeCard).where(...))
    sqlite3.IntegrityError: FOREIGN KEY constraint failed
    [SQL: DELETE FROM knowledge_cards WHERE knowledge_cards.note_id = ?]

## 根因

`review_logs.card_id` 的外键是 **NO ACTION**（`models/review_log.py`），
而 `purge_note` 只删了"按 quiz_id 关联"的复习记录（第 596-598 行）——
**卡片级复习**（`quiz_id IS NULL`、只带 `card_id`）一行都没删。
于是删卡片时撞外键：**复习过卡片的笔记永远删不掉**。

## 判据

1. 卡片级复习记录的笔记必须能 purge 成功；
2. 被删除卡片的复习记录随卡片一起消失（与"学习进度随卡片存亡"一致）；
3. **提升的核心卡片**（`is_key_point`）的历史必须保留 —— 它不在待删卡片里。
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models.knowledge_card import KnowledgeCard
from app.models.note import Note
from app.models.review_log import ReviewLog

from .test_purge_note_integrity import (
    _exists,
    _make_card,
    _make_note,
    _make_user,
    _purge,
)


async def _make_card_review_log(session_factory, user_id: str, card_id: str, note_id: str) -> str:
    """造一条**卡片级**复习记录：`quiz_id` 为空、只带 `card_id`

    这正是 `POST /api/review/cards/{id}/submit`（四档自评）写入的形状。
    """
    rid = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(
            ReviewLog(
                id=rid,
                user_id=user_id,
                quiz_id=None,  # ← 关键：卡片直接复习没有题目
                card_id=card_id,
                note_id=note_id,
                user_answer="心里默述一遍",
                is_correct=True,
                quality=4,
                self_rating=4,
                # `review_at` 是 NOT NULL 且没有 Python 默认值（DB 端也没有），必须显式给
                review_at=datetime.now(UTC),
                item_type="card",
            )
        )
        await db.commit()
    return rid


@pytest.mark.asyncio
class TestPurgeWithCardLevelReviewLogs:
    async def test_purge_succeeds_when_a_card_has_been_reviewed(self, test_db):
        """★ 复现真机 500：复习过卡片的笔记必须能彻底删除"""
        uid = await _make_user(test_db)
        note_id = await _make_note(test_db, uid)
        card_id = await _make_card(test_db, uid, note_id)
        log_id = await _make_card_review_log(test_db, uid, card_id, note_id)

        # 前提自检：那条复习记录真的只挂在卡片上
        async with test_db() as db:
            row = (await db.execute(select(ReviewLog).where(ReviewLog.id == log_id))).scalars().first()
            assert row is not None and row.quiz_id is None and row.card_id == card_id

        await _purge(test_db, note_id)  # 修复前：IntegrityError

        assert not await _exists(test_db, Note, note_id)
        assert not await _exists(test_db, KnowledgeCard, card_id)
        assert not await _exists(test_db, ReviewLog, log_id), "卡片没了，它的复习记录也没了"

    async def test_promoted_key_card_keeps_its_history(self, test_db):
        """反向判据：被提升的核心卡片（note_id 置空）历史必须保留"""
        uid = await _make_user(test_db)
        note_id = await _make_note(test_db, uid)
        keep_card = await _make_card(test_db, uid, note_id, title="核心卡片")
        keep_log = await _make_card_review_log(test_db, uid, keep_card, note_id)

        # 标记为核心卡片 → purge 时必须被提升而不是删除
        async with test_db() as db:
            card = await db.get(KnowledgeCard, keep_card)
            card.is_key_point = True
            await db.commit()

        async with test_db() as db:
            note = (await db.execute(select(Note).where(Note.id == note_id))).scalars().first()
            from app.services.note_service import purge_note

            await purge_note(db, note, promote_key_cards=True)
            await db.commit()

        async with test_db() as db:
            card = await db.get(KnowledgeCard, keep_card)
            log = await db.get(ReviewLog, keep_log)
        assert card is not None and card.note_id is None, "核心卡片没有被提升"
        assert log is not None, "提升卡片的复习历史被误删了"
