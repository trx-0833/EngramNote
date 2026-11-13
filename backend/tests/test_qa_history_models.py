"""
问答历史的模型与清理（docs/qa-history-plan.md §2 / §6.2）

## 这一层守什么

| 判据 | 为什么它会悄悄坏 |
|---|---|
| 两张表能建出来、能读写 | 新模型忘了在 `models/__init__.py` 注册时 `create_all` **不会建表**，而单测用同一进程照常跑 —— 上线才发现 `no such table` |
| 硬删会话**级联**删轮次 | 靠 SQLite 外键（`ondelete=CASCADE`），而 SQLite 默认不开外键；这里断言的是"服务层真的删干净"，而不是"声明了 CASCADE" |
| 软删后不再出现在列表 | 列表少一个 `deleted_at IS NULL` 就会"删了还在"，而 UI 上看起来只是刷新慢 |
| 保留期 0 = 永久保留 | 与 `purge_old_calls` 同一口径；写反了就是把用户的对话按天删光 |
| 标题/副标题按字符截断 | 中文没有空格，按词切会切在词中间；截断错位在列表里一眼看不出 |

⚠️ 这一组**不测**多轮组装（那是 `test_qa_context.py`）与端点（`test_qa_history_api.py`），
各自只盯自己那一层。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.qa_history import TURN_STATUS_ABORTED, TURN_STATUS_OK, QASession, QATurn
from app.services import qa_history_service as svc

UTC = timezone.utc
NOW = datetime(2026, 11, 13, 10, 0, 0, tzinfo=UTC)


def _uid() -> str:
    return str(uuid.uuid4())


async def _seed_user(db, user_id: str) -> None:
    """直接插一行 users —— 会话表的 user_id 有外键，SQLite 开了 `PRAGMA foreign_keys`"""
    from app.models.user import User

    db.add(
        User(
            id=user_id, email=f"{user_id[:8]}@t.local", username=f"u{user_id[:8]}", hashed_password="x", is_active=True
        )
    )
    await db.commit()


@pytest.mark.asyncio
class TestSessionLifecycle:
    async def test_create_session_sets_title_from_first_question(self, test_db):
        async with test_db() as db:
            uid = _uid()
            await _seed_user(db, uid)
            session = await svc.get_or_create_session(db, user_id=uid, first_question="浮充与均充的区别是什么？" * 10)
            await db.commit()
            assert session is not None
            # 截断到 TITLE_MAX_CHARS 并带省略号
            assert len(session.title) <= svc.TITLE_MAX_CHARS + 1
            assert session.title.endswith("…")
            assert session.turn_count == 0

    async def test_existing_session_is_returned_not_recreated(self, test_db):
        async with test_db() as db:
            uid = _uid()
            await _seed_user(db, uid)
            first = await svc.get_or_create_session(db, user_id=uid, first_question="a")
            await db.commit()
            again = await svc.get_or_create_session(db, user_id=uid, session_id=first.id)
            assert again is not None and again.id == first.id

    async def test_missing_or_foreign_or_deleted_session_returns_none(self, test_db):
        """★ 三种"取不到"必须都返回 None：端点据此回 404（不泄露 id 是否存在）"""
        async with test_db() as db:
            owner, outsider = _uid(), _uid()
            await _seed_user(db, owner)
            await _seed_user(db, outsider)
            session = await svc.get_or_create_session(db, user_id=owner, first_question="q")
            await db.commit()

            assert await svc.get_or_create_session(db, user_id=owner, session_id="does-not-exist") is None
            assert await svc.get_or_create_session(db, user_id=outsider, session_id=session.id) is None

            await svc.soft_delete_session(db, user_id=owner, session_id=session.id)
            await db.commit()
            assert await svc.get_or_create_session(db, user_id=owner, session_id=session.id) is None


@pytest.mark.asyncio
class TestRecordTurn:
    async def test_ok_turn_advances_session_counters(self, test_db):
        async with test_db() as db:
            uid = _uid()
            await _seed_user(db, uid)
            session = await svc.get_or_create_session(db, user_id=uid, first_question="浮充是什么？")
            await svc.record_turn(
                db,
                user_id=uid,
                session=session,
                question="浮充是什么？",
                answer="恒压补偿自放电。",
                sources=[{"note_id": "n1", "chunk_id": "c1", "snippet": "x" * 500, "score": 0.8}],
                context_turns=0,
            )
            await db.commit()

            assert session.turn_count == 1
            assert session.last_question == "浮充是什么？"
            turn = (await db.execute(__import__("sqlalchemy").select(QATurn))).scalars().one()
            assert turn.status == TURN_STATUS_OK
            # ★ 片段截到 200 字：历史里存的是"提示是哪一段"，不是"把笔记复制一份"
            assert len(__import__("json").loads(turn.sources_json)[0]["snippet"]) <= 201

    async def test_failed_turn_is_stored_but_does_not_advance_counters(self, test_db):
        """失败轮次入历史（可查"我问过但没答出来"），但不推进 turn_count / last_question"""
        async with test_db() as db:
            uid = _uid()
            await _seed_user(db, uid)
            session = await svc.get_or_create_session(db, user_id=uid, first_question="q1")
            await svc.record_turn(
                db,
                user_id=uid,
                session=session,
                question="q2",
                answer=None,
                status="failed",
                error_code="LLM_TIMEOUT",
            )
            await db.commit()
            assert session.turn_count == 0
            assert session.last_question == "q1"  # 仍是最初那句（成功的）

    async def test_aborted_turn_keeps_partial_answer(self, test_db):
        async with test_db() as db:
            uid = _uid()
            await _seed_user(db, uid)
            session = await svc.get_or_create_session(db, user_id=uid, first_question="q")
            turn = await svc.record_turn(
                db, user_id=uid, session=session, question="q", answer="答到一半", status=TURN_STATUS_ABORTED
            )
            await db.commit()
            assert turn is not None and turn.answer == "答到一半"

    async def test_write_failure_never_raises(self, test_db):
        """★ 历史是附加功能：写失败只 warning，不能打断已经生成好的答案"""
        async with test_db() as db:
            uid = _uid()
            await _seed_user(db, uid)
            session = await svc.get_or_create_session(db, user_id=uid, first_question="q")
            # 删掉会话让外键失败（模拟写入异常）
            await db.delete(session)
            await db.flush()
            result = await svc.record_turn(db, user_id=uid, session=session, question="q", answer="a")
            assert result is None


@pytest.mark.asyncio
class TestListAndDelete:
    async def test_list_orders_by_updated_at_desc(self, test_db):
        async with test_db() as db:
            uid = _uid()
            await _seed_user(db, uid)
            older = await svc.get_or_create_session(db, user_id=uid, first_question="旧")
            await db.commit()
            newer = await svc.get_or_create_session(db, user_id=uid, first_question="新")
            await db.commit()
            older.updated_at = NOW - timedelta(hours=2)
            newer.updated_at = NOW
            await db.commit()

            items, total = await svc.list_sessions(db, user_id=uid)
            assert total == 2
            assert [s.id for s in items] == [newer.id, older.id]

    async def test_keyword_matches_answer_text_inside_turns(self, test_db):
        """★ 用户记住的往往是答案里的那句话，只搜标题等于搜不到"""
        async with test_db() as db:
            uid = _uid()
            await _seed_user(db, uid)
            session = await svc.get_or_create_session(db, user_id=uid, first_question="无关的标题")
            await svc.record_turn(db, user_id=uid, session=session, question="问", answer="硫化会让容量下降")
            await db.commit()

            items, total = await svc.list_sessions(db, user_id=uid, keyword="容量下降")
            assert total == 1 and items[0].id == session.id
            items2, total2 = await svc.list_sessions(db, user_id=uid, keyword="根本不存在")
            assert total2 == 0

    async def test_soft_deleted_hidden_from_list_but_row_kept(self, test_db):
        async with test_db() as db:
            uid = _uid()
            await _seed_user(db, uid)
            session = await svc.get_or_create_session(db, user_id=uid, first_question="q")
            await db.commit()
            assert await svc.soft_delete_session(db, user_id=uid, session_id=session.id) is True
            await db.commit()

            items, total = await svc.list_sessions(db, user_id=uid)
            assert total == 0
            # 行还在（硬删由保留期任务负责）
            from sqlalchemy import select

            assert (await db.execute(select(QASession).where(QASession.id == session.id))).scalars().first() is not None
            # 再删一次返回 False（端点转 404）
            assert await svc.soft_delete_session(db, user_id=uid, session_id=session.id) is False

    async def test_soft_delete_all_returns_count(self, test_db):
        """「清空全部问答历史」入口"""
        async with test_db() as db:
            uid = _uid()
            await _seed_user(db, uid)
            for i in range(3):
                await svc.get_or_create_session(db, user_id=uid, first_question=f"q{i}")
            await db.commit()
            assert await svc.soft_delete_all_sessions(db, user_id=uid) == 3
            await db.commit()
            items, total = await svc.list_sessions(db, user_id=uid)
            assert total == 0 and items == []

    async def test_hard_delete_cascades_turns(self, test_db):
        """★ CASCADE 是数据库行为，这里断言的是"服务层删完之后真的没有孤儿轮次" """
        async with test_db() as db:
            uid = _uid()
            await _seed_user(db, uid)
            session = await svc.get_or_create_session(db, user_id=uid, first_question="q")
            await svc.record_turn(db, user_id=uid, session=session, question="q", answer="a")
            await db.commit()

            session.deleted_at = NOW - timedelta(days=40)
            await db.commit()
            purged = await svc.purge_soft_deleted(db, now=NOW)
            await db.commit()

            from sqlalchemy import func, select

            assert purged == 1
            remaining = (await db.execute(select(func.count()).select_from(QATurn))).scalar_one()
            assert remaining == 0


@pytest.mark.asyncio
class TestRetention:
    async def test_expired_sessions_are_purged(self, test_db):
        async with test_db() as db:
            uid = _uid()
            await _seed_user(db, uid)
            old = await svc.get_or_create_session(db, user_id=uid, first_question="旧")
            fresh = await svc.get_or_create_session(db, user_id=uid, first_question="新")
            await db.commit()
            old.updated_at = NOW - timedelta(days=120)
            fresh.updated_at = NOW - timedelta(days=1)
            await db.commit()

            purged = await svc.purge_expired_sessions(db, retention_days=90, now=NOW)
            await db.commit()
            assert purged == 1
            items, total = await svc.list_sessions(db, user_id=uid)
            assert total == 1 and items[0].id == fresh.id

    async def test_zero_retention_means_keep_forever(self, test_db):
        """★ 写反了就是把用户的对话按天删光"""
        async with test_db() as db:
            uid = _uid()
            await _seed_user(db, uid)
            session = await svc.get_or_create_session(db, user_id=uid, first_question="q")
            await db.commit()
            session.updated_at = NOW - timedelta(days=9999)
            await db.commit()
            assert await svc.purge_expired_sessions(db, retention_days=0, now=NOW) == 0


@pytest.mark.asyncio
class TestStripSnippets:
    async def test_purged_note_loses_snippet_but_keeps_question(self, test_db):
        """★ 笔记彻底删除后：片段清空（正文副本不能留），问与答保留（那是用户的记录）"""
        async with test_db() as db:
            uid = _uid()
            await _seed_user(db, uid)
            session = await svc.get_or_create_session(db, user_id=uid, first_question="q")
            turn = await svc.record_turn(
                db,
                user_id=uid,
                session=session,
                question="这段说的是什么",
                answer="它说的是浮充。",
                sources=[
                    {"note_id": "note-1", "chunk_id": "c1", "snippet": "浮充是长期恒压运行方式"},
                    {"note_id": "note-2", "chunk_id": "c2", "snippet": "均充是短时升压"},
                ],
            )
            await db.commit()

            changed = await svc.strip_snippets_for_note(db, note_id="note-1")
            await db.commit()

            import json

            assert changed == 1
            await db.refresh(turn)
            sources = json.loads(turn.sources_json)
            by_note = {s["note_id"]: s for s in sources}
            assert by_note["note-1"]["snippet"] == ""  # 片段的正文副本被清掉
            assert by_note["note-1"]["chunk_id"] == "c1"  # 墓碑保留
            assert by_note["note-2"]["snippet"] == "均充是短时升压"  # 其他笔记不受影响
            assert turn.question == "这段说的是什么"
            assert turn.answer == "它说的是浮充。"
