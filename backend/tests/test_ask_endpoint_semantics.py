"""`/ask`（非流式）必须与流式端点**同一套语义**（round8 遗留）。

原来它静默忽略 `scope_note_ids` / `allow_general_knowledge`：字段在基类上，
pydantic 接受，端点什么都不做。与 `retry_of` 当年的坑同形。

这里守三件事：
- 空范围 ⇒ 0 条（`[]` 与 None 不同）；
- `allow_general=True` ⇒ 答案带**代码拼的**标注（不是靠模型写）；
- `allow_general=False`（默认）⇒ 不许出现标注。
"""

from __future__ import annotations

import pytest

from pydantic import ValidationError

from app.schemas.knowledge import QuestionRequest


def _stub_retrieve(svc, monkeypatch, *, sources=None, context=""):
    """把检索换成桩：这些用例守的是语义透传与标注，不是检索质量。

    不桩的话每条要等 Celery 编码超时（90s）—— 实测 4 条用例要跑 183 秒。
    """

    async def _retrieve(question, user_id, allow_note_ids=None, **_kw):
        return {
            "context": context,
            "sources": sources or [],
            "provider": "stub",
            "retrieval_status": "hybrid" if sources else "",
        }

    monkeypatch.setattr(svc, "retrieve_context", _retrieve)


def _auth(uid: str) -> dict:
    from app.services.auth_service import create_access_token

    return {"Authorization": f"Bearer {create_access_token(uid)}"}


@pytest.mark.asyncio
class TestAskHonoursScope:
    async def test_empty_scope_passes_through_as_empty_set(self, test_db, monkeypatch):
        """空列表必须以**空集合**传进检索（不是 None）。

        真实过滤在 `search_chunks` / `search_chunks_fts`（由 `test_qa_scope_filter` 守），
        这里守"端点没把它吃掉、也没变成不限"—— 正是 round8 blocker 的形态。
        """
        from app.services.rag_service import RAGService

        svc = RAGService()
        seen: dict = {}

        async def _retrieve(question, user_id, allow_note_ids=None, **_kw):
            seen["allow"] = allow_note_ids
            return {
                "context": "",
                "sources": [],
                "provider": "stub",
                "retrieval_status": "hybrid",
            }

        monkeypatch.setattr(svc, "retrieve_context", _retrieve)

        r = await svc.answer_question("任何问题", "u-ask", allow_note_ids=set(), allow_general=False)
        assert seen["allow"] == set(), f"空集合没被透传（实际 {seen['allow']!r}）"
        assert r["sources"] == []


@pytest.mark.asyncio
class TestAskHonoursGeneral:
    async def test_prefix_is_added_by_code(self, test_db, monkeypatch):
        from app.services.rag_service import RAGService

        svc = RAGService()

        class _LLM:
            _provider = "stub"

            async def chat(self, *_a, **_kw):
                # 桩**故意不写**标注 —— 验证是代码加的
                return "这是通用知识的回答。"

        monkeypatch.setattr("app.services.rag_service.LLMService", lambda: _LLM())
        _stub_retrieve(svc, monkeypatch)  # 无上下文才能走到 allow_general 分支

        r = await svc.answer_question("量子色动力学", "u-ask", allow_note_ids=None, allow_general=True)
        assert r["answer"].startswith("【以下内容来自模型通用知识"), r["answer"]
        assert r["grounded"] is False

    async def test_default_off_has_no_prefix(self, test_db, monkeypatch):
        from app.services.rag_service import RAGService

        svc = RAGService()
        _stub_retrieve(svc, monkeypatch)
        r = await svc.answer_question("量子色动力学", "u-ask")
        assert "模型通用知识" not in r["answer"]


class TestSchemaStillForbidsUnknown:
    def test_extra_field_is_rejected(self):
        """`extra=forbid` 还在：拼错字段名会 422，而不是静默丢弃"""
        with pytest.raises(ValidationError):
            QuestionRequest(question="q", scoped_note_ids=["x"])


@pytest.mark.asyncio
class TestAskEndpointWiring:
    """端点级：`/ask` 里的交集与空列表判定（round9 补）"""

    async def test_ask_endpoint_passes_empty_scope(self, test_db, monkeypatch):
        """`scope_note_ids: []` ⇒ service 必须收到**空集合**（不是 None）"""
        from fastapi.testclient import TestClient
        from sqlalchemy import insert

        from app.api import understanding
        from app.main import app
        from app.models.user import User

        uid = "user-ask-ep"
        async with test_db() as db:
            await db.execute(
                insert(User).values(id=uid, email="ep@t.local", username="ep", hashed_password="x", is_active=True)
            )
            await db.commit()

        seen: dict = {}

        class _RAG:
            def __init__(self, *_a, **_kw):
                pass

            async def answer_question(self, **kw):
                seen.update(kw)
                return {
                    "answer": "答",
                    "sources": [],
                    "provider": "stub",
                    "retrieval_status": "",
                }

        monkeypatch.setattr(understanding, "RAGService", _RAG)

        with TestClient(app) as client:
            r = client.post(
                "/api/understanding/ask",
                json={"question": "问", "scope_note_ids": []},
                headers=_auth(uid),
            )
        assert r.status_code == 200, r.text
        assert seen.get("allow_note_ids") == set(), f"端点没把空列表当空集合（实际 {seen.get('allow_note_ids')!r}）"

    async def test_ask_endpoint_passes_none_when_absent(self, test_db, monkeypatch):
        """不传 ⇒ service 收到 None（= 不限），与 `[]` 必须不同"""
        from fastapi.testclient import TestClient
        from sqlalchemy import insert

        from app.api import understanding
        from app.main import app
        from app.models.user import User

        uid = "user-ask-ep2"
        async with test_db() as db:
            await db.execute(
                insert(User).values(id=uid, email="ep2@t.local", username="ep2", hashed_password="x", is_active=True)
            )
            await db.commit()

        seen: dict = {}

        class _RAG:
            def __init__(self, *_a, **_kw):
                pass

            async def answer_question(self, **kw):
                seen.update(kw)
                return {"answer": "答", "sources": [], "provider": "s", "retrieval_status": ""}

        monkeypatch.setattr(understanding, "RAGService", _RAG)

        with TestClient(app) as client:
            r = client.post("/api/understanding/ask", json={"question": "问"}, headers=_auth(uid))
        assert r.status_code == 200, r.text
        assert seen.get("allow_note_ids") is None
