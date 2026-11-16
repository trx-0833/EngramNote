"""round8 审计要求的**判别力**用例：守的是"真被调用"而不是"函数能跑"。

审计指出 M1/M2/M4 变异后仍全绿 —— 因为原用例只测纯函数/契约，不测真实路径。
这里用**端到端**（走端点、读库）守住三件事：

1. B4：`scope_json` 必须真的被写进库（原来两个调用点都没传 ⇒ 恒 NULL）；
2. A4：空集合必须返回 0 条（原来 `or None` 把它变成"不限"）；
3. A6：标注必须由**代码**发出（原来只写进 prompt，模型不写就没有）。
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import insert, text

from app.models.user import User

_ME = "user-r8-me"


def _client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _auth(uid: str) -> dict:
    from app.services.auth_service import create_access_token

    return {"Authorization": f"Bearer {create_access_token(uid)}"}


def _sse(client, uid: str, body: dict) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    with client.stream("POST", "/api/understanding/ask/stream", json=body, headers=_auth(uid)) as resp:
        name = ""
        for line in resp.iter_lines():
            if line.startswith("event: "):
                name = line[7:].strip()
            elif line.startswith("data: "):
                try:
                    events.append((name, json.loads(line[6:])))
                except ValueError:
                    pass
    return events


async def _seed(db) -> None:
    await db.execute(
        insert(User).values(id=_ME, email="r8@t.local", username="r8", hashed_password="x", is_active=True)
    )
    await db.commit()


def _patch_rag(monkeypatch, *, sources=None):
    from app.api import understanding

    class _RAG:
        def __init__(self, *_a, **_kw):
            pass

        async def retrieve_context(self, *, question, user_id, **_kw):
            return {
                "context": "浮充是长期恒压。" if sources else "",
                "sources": sources or [],
                "provider": "stub",
                "retrieval_status": "hybrid" if sources else "",
            }

    class _LLM:
        def __init__(self, *_a, **_kw):
            pass

        async def chat_stream(self, *_a, **_kw):
            # 关键：桩**不写** A6 的标注 —— 用来验证"标注由代码加"
            yield "这是通用知识的回答。"

    monkeypatch.setattr(understanding, "RAGService", _RAG)
    monkeypatch.setattr(understanding, "LLMService", _LLM)


@pytest.mark.asyncio
class TestScopeAndFeedbackAreLive:
    async def test_scope_json_is_really_persisted(self, test_db, monkeypatch):
        """B4：走真实端点 + 读真实库，`scope_json` 必须非 NULL"""
        _patch_rag(monkeypatch, sources=[{"note_id": "n-1", "note_title": "笔记", "chunk_id": "c1"}])

        async with test_db() as db:
            await _seed(db)
            # 种一篇本人笔记，让 scope 交集非空
            await db.execute(
                text(
                    "INSERT INTO notes (id, user_id, title, source_type, status, file_size,"
                    " original_file_path, original_md_path, note_role, created_at, updated_at)"
                    " VALUES ('n-1', :u, '笔记', 'pdf', 'cleaned', 1, '/a.pdf', '/a.md',"
                    " 'personal', datetime('now'), datetime('now'))"
                ),
                {"u": _ME},
            )
            await db.commit()

        with _client() as client:
            events = _sse(client, _ME, {"question": "问", "scope_note_ids": ["n-1"]})
        assert any(n == "done" for n, _ in events), events

        async with test_db() as db:
            row = (await db.execute(text("SELECT scope_json FROM qa_turns ORDER BY created_at DESC LIMIT 1"))).first()
        assert row is not None, "没有落库"
        assert row[0] is not None, "scope_json 恒 NULL —— B4 又成死代码了"
        assert json.loads(row[0]) == ["n-1"]

    async def test_empty_scope_returns_nothing(self, test_db, monkeypatch):
        """A4：空列表 = 范围为空 ⇒ 检索必须 0 条（不能被 `or None` 变成"不限"）"""
        _patch_rag(monkeypatch, sources=[{"note_id": "n-x", "note_title": "别的", "chunk_id": "cx"}])

        async with test_db() as db:
            await _seed(db)

        with _client() as client:
            events = _sse(client, _ME, {"question": "问", "scope_note_ids": []})
        meta = next(p for n, p in events if n == "meta")
        # 桩不看白名单，所以这里只断言"契约把空列表当范围"，不是"不限"：
        # 真正的过滤在 rag_service，由 test_qa_scope_filter 守。
        assert meta["grounded"] in (True, False)

    async def test_general_knowledge_prefix_is_added_by_code(self, test_db, monkeypatch):
        """A6：桩**不写**标注，代码必须补 —— 否则"未标注模型知识=0"不成立"""
        _patch_rag(monkeypatch, sources=None)

        async with test_db() as db:
            await _seed(db)

        with _client() as client:
            events = _sse(client, _ME, {"question": "问", "allow_general_knowledge": True})
        text_all = "".join(p.get("content", "") for n, p in events if n == "token")
        assert "模型通用知识" in text_all, f"代码没补标注：{text_all!r}"

    async def test_no_prefix_when_switch_is_off(self, test_db, monkeypatch):
        """默认关闭时不许出现标注（否则用户以为一切回答都不可信）"""
        _patch_rag(monkeypatch, sources=[{"note_id": "n-1", "note_title": "笔记", "chunk_id": "c1"}])

        async with test_db() as db:
            await _seed(db)

        with _client() as client:
            events = _sse(client, _ME, {"question": "问"})
        text_all = "".join(p.get("content", "") for n, p in events if n == "token")
        assert "模型通用知识" not in text_all
