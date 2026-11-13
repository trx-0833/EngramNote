"""
第二轮审计（2026-11-13）发现的回归钉子

报告：`_agenttmp/audit-report-2.md`（1 major / 15 minor / 5 nit，前端部分在前端测试里）。
本文件只钉**后端**那几条，每条都对应一个曾经真实存在的缺陷：

| 用例 | 审计编号 | 改前会怎样 |
|---|---|---|
| `%` / `_` 关键词不命中全部 | MINOR-3 | `LIKE '%%'` 命中全部会话，搜索像失效 |
| 纯空白关键词等价于不过滤 | MINOR-3 | `strip()` 后 `%%`，同样命中全部 |
| 浮层给了取不到的 `session_id` → 404 | MINOR-1 | 静默新建一条会话，客户端以为续上了 |
| 浮层拿另一篇笔记的会话 → mismatch 404 | MINOR-1 | 内容写进"note A 的对话"，归属错位 |
| `max_turns=0` 真的不带历史 | MINOR-2 | `or 6` 把 0 当成没配，反而带 6 轮 |
| `max_chars=0` 表示不限总量 | MINOR-2 / MINOR-8 | 预算 0 会把历史全丢光 |
| 空语料时追问只检索一次 | MINOR-8 | 每轮追问白跑两遍 embedding |
| 详情 `turns_limit` 只给最近 N 轮 | MINOR-6 | 上千轮一次性读进内存 |
| 超长问题 → 422 | MINOR-13 | 几百 KB 直接进库、进 prompt |
| 列表下发 `limits` 的生效值 | MINOR-9 | 前端只能写死常量，改配置不跟随 |
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from app.models.note import Note, NoteStatus, SourceType
from app.models.user import User
from app.services import qa_history_service as svc
from app.services.auth_service import create_access_token


def _client() -> TestClient:
    from app.main import app

    return TestClient(app, client=("198.51.100.77", 9777))


def _auth(uid: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(uid)}"}


async def _seed_user(db, uid: str) -> None:
    await db.execute(
        insert(User).values(
            id=uid,
            email=f"{uid[:8]}@t.local",
            username=f"u{uid[:8]}",
            hashed_password="x",
            is_active=True,
        )
    )
    await db.commit()


@pytest.mark.asyncio
class TestKeywordEscaping:
    async def test_wildcards_do_not_match_everything(self, test_db):
        uid = str(uuid.uuid4())
        async with test_db() as db:
            await _seed_user(db, uid)
            session = await svc.get_or_create_session(db, user_id=uid, first_question="浮充是什么")
            await svc.record_turn(db, user_id=uid, session=session, question="浮充是什么", answer="恒压")
            await db.commit()

        async with test_db() as db:
            # 两个字面量通配符都不能命中会话
            for wildcard in ("%", "_", "%%", "50%"):
                items, total = await svc.list_sessions(db, user_id=uid, keyword=wildcard)
                assert total == 0, f"keyword={wildcard!r} 命中了 {total} 条（LIKE 未转义）"
            # 正常词仍然命中
            items, total = await svc.list_sessions(db, user_id=uid, keyword="浮充")
            assert total == 1

    async def test_blank_keyword_behaves_like_no_keyword(self, test_db):
        uid = str(uuid.uuid4())
        async with test_db() as db:
            await _seed_user(db, uid)
            await svc.get_or_create_session(db, user_id=uid, first_question="q")
            await db.commit()
        async with test_db() as db:
            _items, total = await svc.list_sessions(db, user_id=uid, keyword="   ")
            assert total == 1, "纯空白应当等价于不过滤（原来会变成 LIKE '%%' 之外的空条件）"


@pytest.mark.asyncio
class TestNoteSessionStrictness:
    async def _seed_note(self, db, uid: str, note_id: str, title: str) -> None:
        await db.execute(
            insert(Note).values(
                id=note_id,
                user_id=uid,
                title=title,
                source_type=SourceType.pdf.value,
                status=NoteStatus.cleaned.value,
            )
        )
        await db.commit()

    async def test_note_ask_rejects_unknown_session_id(self, test_db, monkeypatch):
        from app.api.notes import ask as note_ask

        uid = str(uuid.uuid4())
        nid = str(uuid.uuid4())
        async with test_db() as db:
            await _seed_user(db, uid)
            await self._seed_note(db, uid, nid, "笔记 A")

        class _Note:
            id = nid
            title = "笔记 A"

        async def _fake_note(*_a, **_k):
            return _Note()

        monkeypatch.setattr(note_ask, "get_note_detail", _fake_note)
        client = _client()
        resp = client.post(
            f"/api/notes/{nid}/ask/stream",
            json={"question": "这段什么意思", "selected_text": "浮充", "session_id": "does-not-exist"},
            headers=_auth(uid),
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "QA_SESSION_NOT_FOUND"
        # 关键：**没有**静默新建会话
        async with test_db() as db:
            sessions = (await db.execute(select(svc.QASession))).scalars().all()
        assert sessions == []

    async def test_note_ask_rejects_session_of_another_note(self, test_db, monkeypatch):
        from app.api.notes import ask as note_ask

        uid = str(uuid.uuid4())
        note_a, note_b = str(uuid.uuid4()), str(uuid.uuid4())
        async with test_db() as db:
            await _seed_user(db, uid)
            await self._seed_note(db, uid, note_a, "笔记 A")
            await self._seed_note(db, uid, note_b, "笔记 B")
            session_a = await svc.get_or_create_session_for_note(db, user_id=uid, note_id=note_a, question="A 里的问题")
            await db.commit()
            session_a_id = session_a.id

        class _Note:
            id = note_b
            title = "笔记 B"

        async def _fake_note(*_a, **_k):
            return _Note()

        monkeypatch.setattr(note_ask, "get_note_detail", _fake_note)
        client = _client()
        resp = client.post(
            f"/api/notes/{note_b}/ask/stream",
            json={"question": "B 里的问题", "selected_text": "均充", "session_id": session_a_id},
            headers=_auth(uid),
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "QA_SESSION_SURFACE_MISMATCH"


@pytest.mark.asyncio
class TestDetailAndLimits:
    async def test_turns_limit_returns_only_recent_turns_ascending(self, test_db):
        uid = str(uuid.uuid4())
        async with test_db() as db:
            await _seed_user(db, uid)
            session = await svc.get_or_create_session(db, user_id=uid, first_question="q0")
            for i in range(3):
                await svc.record_turn(db, user_id=uid, session=session, question=f"q{i}", answer=f"a{i}")
            await db.commit()
            sid = session.id

        client = _client()
        resp = client.get(f"/api/qa/sessions/{sid}?turns_limit=2", headers=_auth(uid))
        assert resp.status_code == 200
        turns = resp.json()["turns"]
        assert [t["question"] for t in turns] == ["q1", "q2"], "应取最近两轮且保持升序"

    async def test_list_limits_reflect_effective_settings(self, test_db, monkeypatch):
        from app.config import get_settings

        uid = str(uuid.uuid4())
        async with test_db() as db:
            await _seed_user(db, uid)
        settings = get_settings()
        monkeypatch.setattr(settings, "qa_context_max_turns", 3, raising=False)
        monkeypatch.setattr(settings, "qa_history_retention_days", 30, raising=False)

        client = _client()
        body = client.get("/api/qa/sessions", headers=_auth(uid)).json()
        assert body["limits"] == {"context_turns": 3, "retention_days": 30}


@pytest.mark.asyncio
class TestInputLimits:
    async def test_overlong_question_is_422(self, test_db):
        uid = str(uuid.uuid4())
        async with test_db() as db:
            await _seed_user(db, uid)
        client = _client()
        resp = client.post(
            "/api/understanding/ask/stream",
            json={"question": "x" * 4001},
            headers=_auth(uid),
        )
        assert resp.status_code == 422
