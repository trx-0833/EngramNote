"""
问答历史端点（docs/qa-history-plan.md §3 / §8.4）

## 判据只有四类

状态码、错误码、分页元数据、以及"归属校验不泄露存在性"。

## 为什么端点测试要直接建行而不走写入端点

写入发生在**流式回答的收尾**（`api/understanding.py`），要从测试里驱动它得
搭一套假 LLM + SSE 读取器；而那套的判据是"落库有没有发生"，属于
`test_ask_stream_persists_turn.py`。这里只关心读取与删除，所以直接用服务层
`record_turn` 造数据 —— **不是**为了省事，是因为"读取层的判据不该依赖写入层的实现"。
"""

from __future__ import annotations

import uuid
from datetime import timezone

import pytest
from fastapi.testclient import TestClient

from app.models.user import User
from app.services import qa_history_service as svc
from app.services.auth_service import create_access_token

UTC = timezone.utc


def _client() -> TestClient:
    from app.main import app

    return TestClient(app, client=("198.51.100.41", 9411))


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


async def _make_session(session_factory, *, count: int = 1, surface: str = "global") -> tuple[str, list[str]]:
    """建一个用户 + 若干会话，返回 (user_id, session_ids)

    ⚠️ 必须是 `async` 并在测试里 `await`：测试类是 `@pytest.mark.asyncio` 的，
    里面已经在事件循环中，再 `asyncio.run(...)` 会抛
    `RuntimeError: asyncio.run() cannot be called from a running event loop`（实测踩过）。
    """
    uid = str(uuid.uuid4())
    ids: list[str] = []

    async with session_factory() as db:
        await _seed_user(db, uid)
        await db.commit()
    for i in range(count):
        async with session_factory() as db:
            session = await svc.get_or_create_session(db, user_id=uid, surface=surface, first_question=f"问题{i}")
            await svc.record_turn(
                db,
                user_id=uid,
                session=session,
                question=f"问题{i}",
                answer=f"答案{i}",
                sources=[{"note_id": "n1", "chunk_id": "c1", "snippet": "浮充", "score": 0.9}],
            )
            await db.commit()
            ids.append(session.id)
    return uid, ids


@pytest.mark.asyncio
class TestListEndpoint:
    async def test_requires_auth(self, test_db):
        client = _client()
        await _make_session(test_db)
        assert client.get("/api/qa/sessions").status_code == 401

    async def test_empty_list_shape(self, test_db):
        client = _client()
        uid, _ = await _make_session(test_db, count=0)
        resp = client.get("/api/qa/sessions", headers=_auth(uid))
        assert resp.status_code == 200
        assert resp.json() == {"items": [], "total": 0, "page": 1, "page_size": 20}

    async def test_lists_own_sessions_with_pagination(self, test_db):
        client = _client()
        uid, ids = await _make_session(test_db, count=3)
        resp = client.get("/api/qa/sessions?page=1&page_size=2", headers=_auth(uid))
        body = resp.json()
        assert resp.status_code == 200
        assert body["total"] == 3 and len(body["items"]) == 2 and body["page_size"] == 2

    async def test_page_size_over_limit_is_422(self, test_db):
        client = _client()
        uid, _ = await _make_session(test_db)
        assert client.get("/api/qa/sessions?page_size=101", headers=_auth(uid)).status_code == 422
        assert client.get("/api/qa/sessions?page=0", headers=_auth(uid)).status_code == 422

    async def test_invalid_surface_is_422(self, test_db):
        client = _client()
        uid, _ = await _make_session(test_db)
        assert client.get("/api/qa/sessions?surface=bogus", headers=_auth(uid)).status_code == 422

    async def test_other_users_sessions_are_invisible(self, test_db):
        client = _client()
        _, mine = await _make_session(test_db, count=1)
        outsider, _ = await _make_session(test_db, count=2)
        body = client.get("/api/qa/sessions", headers=_auth(outsider)).json()
        assert body["total"] == 2
        assert mine[0] not in [item["id"] for item in body["items"]]


@pytest.mark.asyncio
class TestDetailEndpoint:
    async def test_detail_returns_session_and_turns(self, test_db):
        client = _client()
        uid, ids = await _make_session(test_db, count=1)
        resp = client.get(f"/api/qa/sessions/{ids[0]}", headers=_auth(uid))
        body = resp.json()
        assert resp.status_code == 200
        assert body["session"]["id"] == ids[0]
        assert len(body["turns"]) == 1
        turn = body["turns"][0]
        assert turn["question"] == "问题0"
        assert turn["status"] == "ok"
        # sources 从 sources_json 反序列化回列表（不是字符串）
        assert isinstance(turn["sources"], list) and turn["sources"][0]["chunk_id"] == "c1"

    async def test_other_users_session_is_404(self, test_db):
        client = _client()
        _, mine = await _make_session(test_db, count=1)
        outsider, _ = await _make_session(test_db, count=0)
        resp = client.get(f"/api/qa/sessions/{mine[0]}", headers=_auth(outsider))
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "QA_SESSION_NOT_FOUND"

    async def test_unknown_id_is_same_404(self, test_db):
        """★ 不存在与"别人的"共用同一个码：不泄露 id 是否存在"""
        client = _client()
        uid, _ = await _make_session(test_db, count=0)
        resp = client.get("/api/qa/sessions/does-not-exist", headers=_auth(uid))
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "QA_SESSION_NOT_FOUND"


@pytest.mark.asyncio
class TestDeleteEndpoints:
    async def test_delete_then_it_disappears_and_second_delete_is_404(self, test_db):
        client = _client()
        uid, ids = await _make_session(test_db, count=1)

        assert client.delete(f"/api/qa/sessions/{ids[0]}", headers=_auth(uid)).status_code == 204
        assert client.get("/api/qa/sessions", headers=_auth(uid)).json()["total"] == 0
        again = client.delete(f"/api/qa/sessions/{ids[0]}", headers=_auth(uid))
        assert again.status_code == 404
        assert again.json()["error_code"] == "QA_SESSION_NOT_FOUND"

    async def test_clear_all_returns_deleted_count(self, test_db):
        client = _client()
        uid, _ = await _make_session(test_db, count=3)
        resp = client.delete("/api/qa/sessions", headers=_auth(uid))
        assert resp.status_code == 200
        assert resp.json() == {"deleted": 3}
        assert client.get("/api/qa/sessions", headers=_auth(uid)).json()["total"] == 0

    async def test_clear_all_on_empty_is_zero(self, test_db):
        client = _client()
        uid, _ = await _make_session(test_db, count=0)
        assert client.delete("/api/qa/sessions", headers=_auth(uid)).json() == {"deleted": 0}
