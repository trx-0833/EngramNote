"""
流式问答的落库与多轮（docs/qa-history-plan.md §4.5 / §8.5）

## 这一层为什么不能只靠服务层测试

服务层测的是"给一行数据能不能写对"；这里是**接线**：SSE 生成器在三种结局下
（正常结束 / LLM 报错 / 客户端取消）到底有没有真的落库，以及第二轮请求有没有
把第一轮带进 `messages`。接线漏了的时候，服务层测试照样全绿 ——
这正是本次改动最容易静默出错的地方。

## 怎么测

`TestClient.stream()` 驱动真实的 SSE 端点，把 `RAGService.retrieve_context` 与
`LLMService.chat_stream` 换成桩（不联网、不花额度）。

**取消**那一条无法用 HTTP 客户端稳定复现（`break` 触发的 `GeneratorExit` 不是
`CancelledError`，而后者才是"客户端断开"的真实形态），所以直接取出端点的
异步生成器并 `athrow(asyncio.CancelledError())` —— 那正是框架在断连时做的事。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.qa_history import TURN_STATUS_ABORTED, TURN_STATUS_FAILED, TURN_STATUS_OK, QASession, QATurn
from app.models.user import User
from app.schemas.knowledge import QuestionRequest
from app.services.auth_service import create_access_token

UTC = timezone.utc


def _client() -> TestClient:
    from app.main import app

    return TestClient(app, client=("198.51.100.51", 9511))


def _auth(user_id: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


async def _seed_user(db, user_id: str) -> None:
    db.add(
        User(
            id=user_id,
            email=f"{user_id[:8]}@t.local",
            username=f"u{user_id[:8]}",
            hashed_password="x",
            is_active=True,
        )
    )


async def _make_user(session_factory) -> str:
    uid = str(uuid.uuid4())
    async with session_factory() as db:
        await _seed_user(db, uid)
        await db.commit()
    return uid


async def _read_turns(session_factory) -> list[QATurn]:
    async with session_factory() as db:
        rows = (await db.execute(select(QATurn).order_by(QATurn.created_at.asc()))).scalars().all()
        return list(rows)


async def _read_sessions(session_factory) -> list[QASession]:
    async with session_factory() as db:
        return list((await db.execute(select(QASession))).scalars().all())


def _patch_rag(monkeypatch, *, context: str = "浮充是长期恒压运行方式。", sources=None, calls=None):
    """把检索换成桩；`calls` 记录每次的 question（用来断言"追问用了拼接后的检索词"）"""
    from app.api import understanding

    captured = calls if calls is not None else []

    class _RAG:
        async def retrieve_context(self, *, question, user_id):
            captured.append(question)
            return {
                "context": context,
                "sources": sources
                if sources is not None
                else [
                    {
                        "note_id": "n1",
                        "note_title": "电池",
                        "chunk_id": "c1",
                        "relevant_text": "浮充",
                        "snippet": "浮充",
                    }
                ],
                "provider": "stub",
                "retrieval_status": "full_vector",
            }

    monkeypatch.setattr(understanding, "RAGService", _RAG)
    return captured


def _patch_llm(monkeypatch, *, chunks=("浮充", "是恒压。"), raises: Exception | None = None, captured_messages=None):
    from app.api import understanding

    seen = captured_messages if captured_messages is not None else []

    class _LLM:
        async def chat_stream(self, messages, scene="rag_answer_stream"):
            seen.append(messages)
            if raises is not None:
                raise raises
            for chunk in chunks:
                yield chunk

    monkeypatch.setattr(understanding, "LLMService", _LLM)
    return seen


def _stream_events(client: TestClient, user_id: str, *, question: str = "浮充是什么？", session_id=None):
    body = {"question": question}
    if session_id is not None:
        body["session_id"] = session_id
    events: list[tuple[str, dict]] = []
    with client.stream("POST", "/api/understanding/ask/stream", json=body, headers=_auth(user_id)) as resp:
        assert resp.status_code == 200, resp.read()
        current = None
        for line in resp.iter_lines():
            if line.startswith("event: "):
                current = line[len("event: ") :]
            elif line.startswith("data: ") and current:
                events.append((current, json.loads(line[len("data: ") :])))
                current = None
    return events


@pytest.mark.asyncio
class TestStreamPersistence:
    async def test_ok_stream_persists_turn_and_meta_carries_session(self, test_db, monkeypatch):
        client = _client()
        uid = await _make_user(test_db)
        _patch_rag(monkeypatch)
        _patch_llm(monkeypatch)

        events = _stream_events(client, uid)

        meta = next(payload for name, payload in events if name == "meta")
        assert meta["session_id"], "meta 必须带回 session_id（前端据此写 ?s=）"
        assert meta["history_used"] == 0
        assert [name for name, _ in events][-1] == "done"

        turns = await _read_turns(test_db)
        assert len(turns) == 1
        assert turns[0].status == TURN_STATUS_OK
        assert turns[0].answer == "浮充是恒压。"
        assert turns[0].retrieval_query == "浮充是什么？"
        assert turns[0].context_turns == 0
        assert turns[0].session_id == meta["session_id"]

    async def test_second_turn_carries_first_turn_into_messages(self, test_db, monkeypatch):
        """★ 多轮的核心判据：第二轮的 messages 里必须出现第一轮"""
        client = _client()
        uid = await _make_user(test_db)
        _patch_rag(monkeypatch)
        captured: list = []
        _patch_llm(monkeypatch, captured_messages=captured)

        first = _stream_events(client, uid, question="浮充是什么？")
        session_id = next(p for name, p in first if name == "meta")["session_id"]

        _stream_events(client, uid, question="那它对寿命有影响吗？", session_id=session_id)

        second_messages = captured[-1]
        roles = [m["role"] for m in second_messages]
        assert roles == ["system", "user", "assistant", "user"]
        assert second_messages[1]["content"] == "浮充是什么？"
        assert second_messages[2]["content"] == "浮充是恒压。"
        # 有历史时 system 末尾必须带上"事实以本轮资料为准"那条硬指令
        assert "以参考资料为准" in second_messages[0]["content"]

        meta2 = next(
            p for name, p in _stream_events(client, uid, question="再问一次", session_id=session_id) if name == "meta"
        )
        assert meta2["history_used"] >= 1

    async def test_retrieval_query_of_followup_is_concatenated(self, test_db, monkeypatch):
        """★ "那它呢"必须带上上一问才检索得到"""
        client = _client()
        uid = await _make_user(test_db)
        calls: list[str] = []
        _patch_rag(monkeypatch, calls=calls)
        _patch_llm(monkeypatch)

        first = _stream_events(client, uid, question="浮充和均充的区别？")
        session_id = next(p for name, p in first if name == "meta")["session_id"]
        _stream_events(client, uid, question="那它对寿命有影响吗？", session_id=session_id)

        # 第 1 次是首轮检索；第 2 次是追问（拼接后）
        assert calls[0] == "浮充和均充的区别？"
        assert calls[1] == "浮充和均充的区别？ 那它对寿命有影响吗？"

    async def test_llm_error_persists_failed_turn(self, test_db, monkeypatch):
        client = _client()
        uid = await _make_user(test_db)
        _patch_rag(monkeypatch)
        _patch_llm(monkeypatch, raises=RuntimeError("boom"))

        events = _stream_events(client, uid)

        assert any(name == "error" for name, _ in events)
        turns = await _read_turns(test_db)
        assert len(turns) == 1
        assert turns[0].status == TURN_STATUS_FAILED
        # 第二轮审计 MINOR-5：落库的是**稳定码**，不是 Python 类名（可聚合、不随驱动改名）
        assert turns[0].error_code == "LLM_UNKNOWN"

    async def test_cancelled_stream_persists_aborted_turn(self, test_db, monkeypatch):
        """★ 用户点「停止生成」的那一轮最需要回看：必须留下 aborted + 已生成部分"""
        from app.api import understanding
        from app.database import get_session_factory

        uid = await _make_user(test_db)
        _patch_rag(monkeypatch)
        _patch_llm(monkeypatch, chunks=("浮充是", "恒压…"))

        # 直接取端点的异步生成器（HTTP 客户端复现不了 CancelledError）
        async with test_db() as db:
            user = (await db.execute(select(User).where(User.id == uid))).scalars().one()
            response = await understanding.ask_question_stream(QuestionRequest(question="浮充是什么？"), user, db)
            gen = response.body_iterator
            first = await gen.__anext__()  # meta
            assert "event: meta" in first
            second = await gen.__anext__()  # 第一个 token
            assert "event: token" in second
            with pytest.raises(asyncio.CancelledError):
                await gen.athrow(asyncio.CancelledError())

        factory = get_session_factory()
        turns = await _read_turns(factory)
        assert len(turns) == 1
        assert turns[0].status == TURN_STATUS_ABORTED
        assert turns[0].answer == "浮充是"  # 已生成的那一段被保留

    async def test_deleted_session_returns_404(self, test_db, monkeypatch):
        """会话已删 → 真 HTTP 404（而不是一个 error 帧），前端据此清掉 ?s="""
        from app.services import qa_history_service as svc

        client = _client()
        uid = await _make_user(test_db)
        _patch_rag(monkeypatch)
        _patch_llm(monkeypatch)

        first = _stream_events(client, uid)
        session_id = next(p for name, p in first if name == "meta")["session_id"]

        async with test_db() as db:
            await svc.soft_delete_session(db, user_id=uid, session_id=session_id)
            await db.commit()

        resp = client.post(
            "/api/understanding/ask/stream",
            json={"question": "还在吗", "session_id": session_id},
            headers=_auth(uid),
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "QA_SESSION_NOT_FOUND"


@pytest.mark.asyncio
class TestHistorySwitches:
    async def test_history_disabled_writes_nothing(self, test_db, monkeypatch):
        """开关关掉时：回答照常，但库里不新增任何会话/轮次"""
        from app.config import get_settings

        client = _client()
        uid = await _make_user(test_db)
        _patch_rag(monkeypatch)
        _patch_llm(monkeypatch)

        settings = get_settings()
        monkeypatch.setattr(settings, "qa_history_enabled", False, raising=False)

        events = _stream_events(client, uid)
        meta = next(p for name, p in events if name == "meta")

        assert meta["session_id"] is None
        assert await _read_turns(test_db) == []
        assert await _read_sessions(test_db) == []

    async def test_multiturn_disabled_still_stores_but_without_context(self, test_db, monkeypatch):
        from app.config import get_settings

        client = _client()
        uid = await _make_user(test_db)
        _patch_rag(monkeypatch)
        captured: list = []
        _patch_llm(monkeypatch, captured_messages=captured)

        settings = get_settings()
        monkeypatch.setattr(settings, "qa_multiturn_enabled", False, raising=False)

        first = _stream_events(client, uid, question="浮充是什么？")
        session_id = next(p for name, p in first if name == "meta")["session_id"]
        second = _stream_events(client, uid, question="那它呢", session_id=session_id)

        meta2 = next(p for name, p in second if name == "meta")
        assert meta2["history_used"] == 0  # 关了多轮：不带上下文
        assert [m["role"] for m in captured[-1]] == ["system", "user"]  # 只有当前问
        # 但历史照存（两轮都在）
        assert len(await _read_turns(test_db)) == 2


@pytest.mark.asyncio
class TestNoteAskPersistence:
    """笔记浮层：**只落库、不带多轮**（计划 §5.3 / 决策 2）"""

    async def test_note_ask_creates_note_session_and_stores_turn(self, test_db, monkeypatch):
        from app.api.notes import ask as note_ask
        from app.database import get_session_factory
        from app.models.note import Note, NoteStatus, SourceType

        uid = await _make_user(test_db)
        nid = str(uuid.uuid4())
        async with test_db() as db:
            db.add(
                Note(
                    id=nid,
                    user_id=uid,
                    title="电池笔记",
                    source_type=SourceType.pdf,
                    status=NoteStatus.cleaned,
                )
            )
            await db.commit()

        class _Note:
            id = nid
            title = "电池笔记"

        async def _fake_note(*_args, **_kwargs):
            return _Note()

        class _LLM:
            async def chat_stream(self, messages, scene="note_ask_stream"):
                for chunk in ("这是", "答案。"):
                    yield chunk

        monkeypatch.setattr(note_ask, "get_note_detail", _fake_note)
        monkeypatch.setattr(note_ask, "LLMService", _LLM)

        client = _client()
        body = {
            "question": "这段什么意思？",
            "selected_text": "浮充是长期恒压运行方式",
            "context_before": "",
            "context_after": "",
        }
        with client.stream("POST", f"/api/notes/{nid}/ask/stream", json=body, headers=_auth(uid)) as resp:
            assert resp.status_code == 200
            events = [line for line in resp.iter_lines() if line.startswith("event: ")]

        assert "event: done" in events
        factory = get_session_factory()
        sessions = await _read_sessions(factory)
        assert len(sessions) == 1
        assert sessions[0].surface == "note"
        assert sessions[0].note_id == nid
        turns = await _read_turns(factory)
        assert len(turns) == 1
        assert turns[0].status == TURN_STATUS_OK
        assert turns[0].answer == "这是答案。"
        # 浮层不做多轮：context_turns 恒为 0
        assert turns[0].context_turns == 0


@pytest.mark.asyncio
class TestAuditRegressions:
    """2026-11-13 独立审计发现的问题，逐条钉住（改前全部为红）"""

    async def test_note_session_cannot_be_continued_from_global_endpoint(self, test_db, monkeypatch):
        """审计 M1：`surface` 必须是**校验**，不能把 global 轮次写进 note 会话"""
        from app.services import qa_history_service as svc

        client = _client()
        uid = await _make_user(test_db)
        _patch_rag(monkeypatch)
        _patch_llm(monkeypatch)

        async with test_db() as db:
            note_session = await svc.get_or_create_session(
                db, user_id=uid, surface="note", note_id="note-x", first_question="浮层里的问题"
            )
            await db.commit()
            note_session_id = note_session.id

        resp = client.post(
            "/api/understanding/ask/stream",
            json={"question": "全库问答", "session_id": note_session_id},
            headers=_auth(uid),
        )
        assert resp.status_code == 404
        # 第二轮审计 MINOR-4：note 会话仍在，只是入口不对 -> 用 mismatch 码，
        # 免得界面把「属于另一个入口」说成「已被删除」
        assert resp.json()["error_code"] == "QA_SESSION_SURFACE_MISMATCH"

    async def test_empty_question_creates_no_session(self, test_db, monkeypatch):
        """审计 m3：空问题原先会留下一条 `turn_count=0` 的「未命名对话」"""
        client = _client()
        uid = await _make_user(test_db)
        _patch_rag(monkeypatch)
        _patch_llm(monkeypatch)

        resp = client.post("/api/understanding/ask/stream", json={"question": "   "}, headers=_auth(uid))
        assert resp.status_code == 422
        assert await _read_sessions(test_db) == []

    async def test_retrieval_query_records_the_query_that_actually_worked(self, test_db, monkeypatch):
        """审计 m2：拼接检索为空、回退用当前问命中时，`retrieval_query` 必须记当前问"""
        from app.api import understanding

        client = _client()
        uid = await _make_user(test_db)
        _patch_llm(monkeypatch)

        calls: list[str] = []

        class _RAG:
            async def retrieve_context(self, *, question, user_id):
                calls.append(question)
                # 拼接词 → 空；只用当前问 → 命中
                if "浮充是什么？" in question and len(question) > len("浮充是什么？"):
                    return {"context": "", "sources": [], "provider": "stub", "retrieval_status": ""}
                return {
                    "context": "浮充是恒压。",
                    "sources": [{"note_id": "n1", "chunk_id": "c1", "relevant_text": "浮充"}],
                    "provider": "stub",
                    "retrieval_status": "full_vector",
                }

        monkeypatch.setattr(understanding, "RAGService", _RAG)

        first = _stream_events(client, uid, question="浮充是什么？")
        session_id = next(p for name, p in first if name == "meta")["session_id"]
        _stream_events(client, uid, question="那它呢", session_id=session_id)

        turns = await _read_turns(test_db)
        assert len(turns) == 2
        # 第 2 轮：拼接词空 → 回退当前问 → 落库的必须是当前问
        assert turns[1].retrieval_query == "那它呢"
        assert calls[-1] == "那它呢"

    async def test_context_turns_records_the_trimmed_count(self, test_db, monkeypatch):
        """审计 m1：超预算时 `context_turns` 必须记**真实**带上几轮，而不是 len(history)"""
        from app.config import get_settings

        client = _client()
        uid = await _make_user(test_db)
        _patch_rag(monkeypatch)
        _patch_llm(monkeypatch)

        settings = get_settings()
        # 让预算必然裁掉一部分：6 轮 × (长问 + 长答) 远超 4000 字符
        monkeypatch.setattr(settings, "qa_context_max_chars", 300, raising=False)
        monkeypatch.setattr(settings, "qa_context_answer_chars", 400, raising=False)

        long_q = "很长的问题" * 40  # 200 字：两轮的问答就超过 300 字预算
        session_id = None
        for i in range(4):
            meta = next(
                p
                for name, p in _stream_events(client, uid, question=f"{long_q}{i}", session_id=session_id)
                if name == "meta"
            )
            session_id = meta["session_id"]

        last = next(
            p
            for name, p in _stream_events(client, uid, question="最后一个问题", session_id=session_id)
            if name == "meta"
        )
        # 5 轮历史，每轮约 200 字，300 字预算 ⇒ 实际只能带 1 轮
        assert 0 < last["history_used"] < 4, last
        turns = await _read_turns(test_db)
        assert turns[-1].context_turns == last["history_used"]
