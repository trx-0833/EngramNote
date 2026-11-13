"""
笔记彻底删除后的问答历史联动（docs/qa-history-plan.md §6.3；2026-11-13 审计 M5）

## 这条用例守的是**接线**，不是函数

`strip_snippets_for_note` 早就写好了、也有单测（`test_qa_history_models.py`），
但**生产路径上没有任何调用方** —— `purge_note` 完全没有提到 qa。静态看像已实现，
实际后果是"用户彻底删除的笔记，正文仍以引用片段的形式在库里留 90 天"。

所以这里从 `purge_note` 入口走一遍真流程，断言：

1. 该笔记的片段被清空（`relevant_text` 变空串）；
2. `chunk_id` 仍在（墓碑，便于以后追"这条引用曾指向哪里"）；
3. 用户自己的 `question` / `answer` **不受影响**（那是他的记录，不是笔记的副本）；
4. 其他笔记的片段不被误伤。
"""

from __future__ import annotations

import json
import uuid
from datetime import timezone

import pytest
from sqlalchemy import insert, select

from app.models.note import Note, NoteStatus, SourceType
from app.models.qa_history import QATurn
from app.models.user import User
from app.services import qa_history_service as qa_svc
from app.services.note_service import purge_note

UTC = timezone.utc


async def _seed(db, uid: str, note_id: str) -> None:
    """插一个用户 + 一篇笔记

    用 `insert().values()` 按**明确顺序**执行（与 `tests/test_chunks.py` 同一写法）：
    `db.add()` 依赖 SQLAlchemy 自己的插入排序，而 `notes.user_id` 有外键 ——
    顺序错了就是 `FOREIGN KEY constraint failed`（本地实测踩过一次）。
    """
    await db.execute(
        insert(User).values(
            id=uid,
            email=f"{uid[:8]}@t.local",
            username=f"u{uid[:8]}",
            hashed_password="x",
            is_active=True,
        )
    )
    await db.execute(
        insert(Note).values(
            id=note_id,
            user_id=uid,
            title="要被彻底删除的笔记",
            source_type=SourceType.pdf.value,
            status=NoteStatus.cleaned.value,
        )
    )
    await db.commit()


@pytest.mark.asyncio
async def test_purge_note_clears_qa_snippets_but_keeps_question(test_db):
    uid = str(uuid.uuid4())
    note_id = str(uuid.uuid4())

    async with test_db() as db:
        await _seed(db, uid, note_id)
        session = await qa_svc.get_or_create_session(db, user_id=uid, first_question="这段讲什么")
        turn = await qa_svc.record_turn(
            db,
            user_id=uid,
            session=session,
            question="这段讲什么？",
            answer="讲的是浮充。",
            sources=[
                {"note_id": note_id, "chunk_id": "c1", "relevant_text": "浮充是长期恒压运行方式"},
                {"note_id": "other-note", "chunk_id": "c2", "relevant_text": "均充是短时升压"},
            ],
        )
        await db.commit()
        assert turn is not None

    # ── 走真实入口：彻底删除笔记 ──
    async with test_db() as db:
        note = (await db.execute(select(Note).where(Note.id == note_id))).scalars().one()
        await purge_note(db, note)
        await db.commit()

    async with test_db() as db:
        turn = (await db.execute(select(QATurn))).scalars().one()
        sources = {item["note_id"]: item for item in json.loads(turn.sources_json)}

        # 1) 被删笔记的**正文副本**必须消失
        assert sources[note_id]["relevant_text"] == ""
        # 2) 墓碑保留
        assert sources[note_id]["chunk_id"] == "c1"
        # 3) 用户自己的问与答不受影响（那是他的记录，不是笔记的副本）
        assert turn.question == "这段讲什么？"
        assert turn.answer == "讲的是浮充。"
        # 4) 其他笔记不受牵连
        assert sources["other-note"]["relevant_text"] == "均充是短时升压"
