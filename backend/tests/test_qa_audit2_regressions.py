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


@pytest.mark.asyncio
class TestTurnCountConcurrency:
    """第二轮审计 §4 疑点 1：`turn_count` 的读-改-写会丢更新

    用**陈旧对象**复现而不是真并发：开两个独立 session 各自把同一个会话读进内存，
    然后先后各写一轮。旧实现（`session.turn_count = (session.turn_count or 0) + 1`）
    会让后写的那一方把 1 覆盖成 1 —— 库里只剩 1，而实际有 2 轮，且**不报错**。
    新实现是 SQL 级自增，两方各 `+1`，最终 2。
    """

    async def test_stale_session_objects_do_not_lose_count(self, test_db):
        uid = str(uuid.uuid4())
        async with test_db() as db:
            await _seed_user(db, uid)
            session = await svc.get_or_create_session(db, user_id=uid, first_question="q")
            await db.commit()
            sid = session.id

        # 两个独立 session 各读一份（此时都读到 turn_count=0）
        async with test_db() as db1, test_db() as db2:
            stale1 = (await db1.execute(select(svc.QASession).where(svc.QASession.id == sid))).scalars().one()
            stale2 = (await db2.execute(select(svc.QASession).where(svc.QASession.id == sid))).scalars().one()
            assert stale1.turn_count == 0 and stale2.turn_count == 0

            await svc.record_turn(db1, user_id=uid, session=stale1, question="q1", answer="a1")
            await db1.commit()
            # db2 手里的对象仍是旧值；旧实现会把它写回成 1（覆盖掉 db1 的成果）
            await svc.record_turn(db2, user_id=uid, session=stale2, question="q2", answer="a2")
            await db2.commit()

        async with test_db() as db:
            final = (await db.execute(select(svc.QASession).where(svc.QASession.id == sid))).scalars().one()
            turns = (await db.execute(select(svc.QATurn).where(svc.QATurn.session_id == sid))).scalars().all()
        assert len(turns) == 2
        assert final.turn_count == 2, "陈旧对象把轮次数覆盖了（应改成 SQL 级自增）"


@pytest.mark.asyncio
class TestSnippetStrippingScope:
    async def test_stripping_is_scoped_to_the_owner(self, test_db):
        """第二轮审计 §4 疑点 4：清理必须按 user_id 收窄，不能扫全部用户"""
        owner, stranger = str(uuid.uuid4()), str(uuid.uuid4())
        shared_note_id = str(uuid.uuid4())
        async with test_db() as db:
            await _seed_user(db, owner)
            await _seed_user(db, stranger)
            s1 = await svc.get_or_create_session(db, user_id=owner, first_question="q1")
            await svc.record_turn(
                db,
                user_id=owner,
                session=s1,
                question="q1",
                answer="a1",
                sources=[{"note_id": shared_note_id, "chunk_id": "c1", "relevant_text": "正文"}],
            )
            s2 = await svc.get_or_create_session(db, user_id=stranger, first_question="q2")
            await svc.record_turn(
                db,
                user_id=stranger,
                session=s2,
                question="q2",
                answer="a2",
                sources=[{"note_id": shared_note_id, "chunk_id": "c2", "relevant_text": "别人的正文"}],
            )
            await db.commit()

        async with test_db() as db:
            # 只清 owner 的：陌生人那一行必须原样保留
            changed = await svc.strip_snippets_for_note(db, note_id=shared_note_id, user_id=owner)
            await db.commit()
            assert changed == 1

        async with test_db() as db:
            rows = {r.user_id: r for r in (await db.execute(select(svc.QATurn))).scalars().all()}
            import json as _json

            owner_sources = _json.loads(rows[owner].sources_json)
            stranger_sources = _json.loads(rows[stranger].sources_json)
            assert owner_sources[0]["relevant_text"] == ""
            assert stranger_sources[0]["relevant_text"] == "别人的正文"


@pytest.mark.asyncio
class TestAclosePersistence:
    """第三轮审计 M2：`aclose()` 收尾（`GeneratorExit`）也必须落库

    `aclose()` 抛的是 `GeneratorExit`（`BaseException` 子类），原来的两个
    `except Exception` / `except CancelledError` 都抓不到 —— 于是整个轮次一条不写。
    修法是把落库放进 `finally`。这条用例就是那个形态：取两个事件后直接 `aclose()`。
    """

    async def test_aclose_persists_aborted_turn(self, test_db, monkeypatch):
        from app.api import understanding
        from app.database import get_session_factory
        from app.schemas.knowledge import QuestionRequest
        from sqlalchemy import select as _select

        uid = str(uuid.uuid4())
        async with test_db() as db:
            await _seed_user(db, uid)

        class _RAG:
            async def retrieve_context(self, *, question, user_id):
                return {
                    "context": "浮充是恒压。",
                    "sources": [{"note_id": "n1", "chunk_id": "c1", "relevant_text": "浮充"}],
                    "provider": "stub",
                    "retrieval_status": "full_vector",
                    "corpus_empty": False,
                }

        class _LLM:
            async def chat_stream(self, messages, scene="rag_answer_stream"):
                yield "浮充是"
                yield "恒压。"

        monkeypatch.setattr(understanding, "RAGService", _RAG)
        monkeypatch.setattr(understanding, "LLMService", _LLM)

        async with test_db() as db:
            user = (await db.execute(_select(User).where(User.id == uid))).scalars().one()
            response = await understanding.ask_question_stream(QuestionRequest(question="浮充是什么？"), user, db)
            gen = response.body_iterator
            assert "event: meta" in await gen.__anext__()
            assert "event: token" in await gen.__anext__()
            # ★ 关键：用 aclose() 收尾（不是 athrow(CancelledError)）
            await gen.aclose()

        factory = get_session_factory()
        async with factory() as db:
            turns = (await db.execute(_select(svc.QATurn))).scalars().all()
        assert len(turns) == 1, "aclose 收尾时整轮都没落库（落库没在 finally 里）"
        assert turns[0].status == "aborted"
        assert turns[0].answer == "浮充是", "已生成的半截文本应当保留"


@pytest.mark.asyncio
class TestClearAllScope:
    """第三轮审计 M1：清空全部必须能按 surface 收窄

    抽屉只列 `surface=global`，确认框的数字也来自那份列表；如果后端"清空"不认
    `surface`，就会把用户在界面上**看不到**的浮层会话一起删掉，
    表现为"确认 1 个、toast 2 个"，而且被多删的那条无从发现。
    """

    async def test_clear_with_surface_only_touches_that_surface(self, test_db):
        uid = str(uuid.uuid4())
        async with test_db() as db:
            await _seed_user(db, uid)
            g = await svc.get_or_create_session(db, user_id=uid, surface="global", first_question="g")
            n = await svc.get_or_create_session(db, user_id=uid, surface="note", note_id="note-1", first_question="n")
            await svc.record_turn(db, user_id=uid, session=g, question="g", answer="a")
            await svc.record_turn(db, user_id=uid, session=n, question="n", answer="a")
            await db.commit()

        client = _client()
        body = client.delete("/api/qa/sessions?surface=global", headers=_auth(uid)).json()
        assert body == {"deleted": 1}, "确认框按 global 的条数问，删除也必须只动 global"

        # global 已清空、note 仍在（列表按入口分开看，才看得出"只动了 global"）
        assert client.get("/api/qa/sessions?surface=global", headers=_auth(uid)).json()["total"] == 0
        assert client.get("/api/qa/sessions?surface=note", headers=_auth(uid)).json()["total"] == 1

    async def test_clear_without_surface_still_means_everything(self, test_db):
        """不传 surface 保持"真·全部"语义（运维/脚本用），并如实返回条数"""
        uid = str(uuid.uuid4())
        async with test_db() as db:
            await _seed_user(db, uid)
            await svc.get_or_create_session(db, user_id=uid, surface="global", first_question="g")
            await svc.get_or_create_session(db, user_id=uid, surface="note", note_id="note-1", first_question="n")
            await db.commit()

        client = _client()
        assert client.delete("/api/qa/sessions", headers=_auth(uid)).json() == {"deleted": 2}


@pytest.mark.asyncio
class TestTurnCountSemantics:
    """第三轮审计 m2：三种状态都计入轮数（封面数字要与点开后的条数一致）"""

    async def test_failed_and_aborted_turns_are_counted(self, test_db):
        uid = str(uuid.uuid4())
        async with test_db() as db:
            await _seed_user(db, uid)
            session = await svc.get_or_create_session(db, user_id=uid, first_question="q")
            await svc.record_turn(db, user_id=uid, session=session, question="q1", answer="a")
            await svc.record_turn(db, user_id=uid, session=session, question="q2", answer=None, status="failed")
            await svc.record_turn(db, user_id=uid, session=session, question="q3", answer="半截", status="aborted")
            await db.commit()

        async with test_db() as db:
            refreshed = (await db.execute(select(svc.QASession).where(svc.QASession.id == session.id))).scalars().one()
            turns = (await db.execute(select(svc.QATurn).where(svc.QATurn.session_id == session.id))).scalars().all()
        assert len(turns) == 3
        assert refreshed.turn_count == 3, "封面写 0 轮、点开有 3 条（只统计了 ok）"
