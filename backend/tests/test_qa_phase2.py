"""
阶段 2 的后端用例：重命名 + 游标分页（`before` / `has_more`）

对应 `docs/qa-deepseek-redesign.md` §3 的「阶段 2」契约。
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from app.models.user import User
from app.services import qa_history_service as svc
from app.services.auth_service import create_access_token


def _client() -> TestClient:
    from app.main import app

    return TestClient(app, client=("198.51.100.88", 9788))


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
class TestRename:
    async def test_rename_updates_title_and_keeps_updated_at(self, test_db):
        uid = str(uuid.uuid4())
        async with test_db() as db:
            await _seed_user(db, uid)
            session = await svc.get_or_create_session(db, user_id=uid, first_question="原始问题")
            await svc.record_turn(db, user_id=uid, session=session, question="原始问题", answer="答")
            await db.commit()
            sid, before_updated_at = session.id, session.updated_at

        client = _client()
        resp = client.patch(f"/api/qa/sessions/{sid}", json={"title": "改过的标题"}, headers=_auth(uid))
        assert resp.status_code == 200
        assert resp.json()["title"] == "改过的标题"

        async with test_db() as db:
            after = (await db.execute(select(svc.QASession).where(svc.QASession.id == sid))).scalars().one()
        assert after.title == "改过的标题"
        # 改名不该把会话顶到列表最前面（这一列是"最后一次真的聊过"）
        assert after.updated_at == before_updated_at

    async def test_rename_rejects_empty_title_and_other_users(self, test_db):
        owner, stranger = str(uuid.uuid4()), str(uuid.uuid4())
        async with test_db() as db:
            await _seed_user(db, owner)
            await _seed_user(db, stranger)
            session = await svc.get_or_create_session(db, user_id=owner, first_question="q")
            await db.commit()
            sid = session.id

        client = _client()
        # 空标题：422（否则界面会退化成"未命名对话"，用户以为改名失败但没提示）
        assert client.patch(f"/api/qa/sessions/{sid}", json={"title": ""}, headers=_auth(owner)).status_code == 422
        # 别人的会话：404（不区分"不存在"与"不是你的"，避免变成探测接口）
        assert client.patch(f"/api/qa/sessions/{sid}", json={"title": "x"}, headers=_auth(stranger)).status_code == 404


@pytest.mark.asyncio
class TestCursorPaging:
    async def _seed_three_turns(self, test_db, uid: str) -> str:
        async with test_db() as db:
            await _seed_user(db, uid)
            session = await svc.get_or_create_session(db, user_id=uid, first_question="q0")
            for i in range(3):
                await svc.record_turn(db, user_id=uid, session=session, question=f"q{i}", answer=f"a{i}")
            await db.commit()
            return session.id

    async def test_before_returns_older_turns_and_reports_has_more(self, test_db):
        uid = str(uuid.uuid4())
        sid = await self._seed_three_turns(test_db, uid)
        client = _client()

        # 最新一页：取最近 2 轮 → q1/q2，且"更早还有 1 轮"
        first = client.get(f"/api/qa/sessions/{sid}?turns_limit=2", headers=_auth(uid)).json()
        assert [t["question"] for t in first["turns"]] == ["q1", "q2"]
        assert first["has_more"] is True

        # 向上翻一页：以 q1 为游标 → 只剩 q0，且没有更早的了
        second = client.get(
            f"/api/qa/sessions/{sid}?turns_limit=2&before={first['turns'][0]['id']}",
            headers=_auth(uid),
        ).json()
        assert [t["question"] for t in second["turns"]] == ["q0"]
        assert second["has_more"] is False

    async def test_unknown_cursor_returns_empty_page(self, test_db):
        """游标不在本会话里 → 空页（而不是悄悄回最新一页，让按钮点了没反应）"""
        uid = str(uuid.uuid4())
        sid = await self._seed_three_turns(test_db, uid)
        client = _client()
        body = client.get(f"/api/qa/sessions/{sid}?before={uuid.uuid4()}", headers=_auth(uid)).json()
        assert body["turns"] == []
        assert body["has_more"] is False


@pytest.mark.asyncio
class TestSupersededVisibility:
    """阶段 3 的地基：被「重新生成」替换掉的旧轮不进详情，且不重复计轮数

    真正的 `retry_of` 端到端接线本轮**未完成**（见 docs/qa-deepseek-redesign.md），
    但"旧轮该怎么被藏起来"是它唯一的产品语义，先把它钉住：
    否则接上 retry_of 时会出现"同一问答显示两份、封面轮数一轮一轮涨"。
    """

    async def test_superseded_turn_is_hidden_and_uncounted(self, test_db):
        from app.models.qa_history import TURN_STATUS_OK, TURN_STATUS_SUPERSEDED

        uid = str(uuid.uuid4())
        async with test_db() as db:
            await _seed_user(db, uid)
            session = await svc.get_or_create_session(db, user_id=uid, first_question="q")
            old = await svc.record_turn(db, user_id=uid, session=session, question="q", answer="旧答案")
            await svc.record_turn(db, user_id=uid, session=session, question="追问", answer="新答案")
            await db.commit()
            sid, old_id = session.id, old.id
            assert session.turn_count == 2

        async with test_db() as db:
            assert await svc.supersede_turn(db, session_id=sid, turn_id=old_id) is True
            await db.commit()
            # 幂等：再标一次不重复扣减
            assert await svc.supersede_turn(db, session_id=sid, turn_id=old_id) is False
            await db.commit()

        client = _client()
        detail = client.get(f"/api/qa/sessions/{sid}", headers=_auth(uid)).json()
        assert [t["question"] for t in detail["turns"]] == ["追问"]
        assert detail["turns"][0]["status"] == TURN_STATUS_OK
        # 轮数跟着减：替换不是新增
        assert detail["session"]["turn_count"] == 1

        async with test_db() as db:
            row = (await db.execute(select(svc.QATurn).where(svc.QATurn.id == old_id))).scalars().one()
        assert row.status == TURN_STATUS_SUPERSEDED
        assert row.answer == "旧答案"  # 数据保留，只是不再展示
