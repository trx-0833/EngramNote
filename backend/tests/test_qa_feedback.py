"""B2（`qa_turns.feedback`）+ B4（`qa_turns.scope_json`）的用例。

两列都是**新加的表结构**（AGENTS §2：可空、旧行不回填、回退 = 保留列不读）。
这里守三件事：
- 反馈能写、能改、能取消；
- **越权 404**（不泄露"这条存在但不是你的"）；
- 迁移真的把两列加进了 `qa_turns`（防止"模型加了列但迁移漏了"）。
"""

from __future__ import annotations

import pytest
from sqlalchemy import insert, text

from app.models.qa_history import QASession, QATurn
from app.models.user import User

_ME = "user-fb-me"
_OTHER = "user-fb-other"


async def _seed_turn(db) -> str:
    for uid, email in ((_ME, "fb-me@t.local"), (_OTHER, "fb-other@t.local")):
        await db.execute(insert(User).values(id=uid, email=email, username=uid, hashed_password="x", is_active=True))
    # qa_turns.session_id 有 FK —— 先种会话，否则 FK 约束失败
    await db.execute(
        insert(QASession).values(
            id="sess-fb-1",
            user_id=_ME,
            surface="global",
            title="反馈测试",
        )
    )
    await db.execute(
        insert(QATurn).values(
            id="turn-fb-1",
            user_id=_ME,
            session_id="sess-fb-1",
            question="浮充是什么",
            answer="长期恒压",
            status="ok",
        )
    )
    await db.commit()
    return "turn-fb-1"


def _auth(uid: str) -> dict:
    from app.services.auth_service import create_access_token

    return {"Authorization": f"Bearer {create_access_token(uid)}"}


@pytest.mark.asyncio
class TestFeedbackEndpoint:
    async def test_set_change_and_clear(self, test_db):
        """能写、能改、能取消（`rating: null`）"""
        from fastapi.testclient import TestClient

        from app.main import app

        async with test_db() as db:
            tid = await _seed_turn(db)

        with TestClient(app) as client:
            for body, expect in (({"rating": "up"}, "up"), ({"rating": "down"}, "down")):
                r = client.post(f"/api/qa/turns/{tid}/feedback", json=body, headers=_auth(_ME))
                assert r.status_code == 204, r.text
                async with test_db() as db:
                    row = (await db.execute(text("SELECT feedback FROM qa_turns WHERE id=:i"), {"i": tid})).first()
                    assert row[0] == expect
            # 取消
            r = client.post(f"/api/qa/turns/{tid}/feedback", json={"rating": None}, headers=_auth(_ME))
            assert r.status_code == 204
            async with test_db() as db:
                row = (await db.execute(text("SELECT feedback FROM qa_turns WHERE id=:i"), {"i": tid})).first()
                assert row[0] is None

    async def test_foreign_turn_is_not_found(self, test_db):
        """越权必须是 404，不能 403（403 会泄露"这条存在"）"""
        from fastapi.testclient import TestClient

        from app.main import app

        async with test_db() as db:
            tid = await _seed_turn(db)

        with TestClient(app) as client:
            r = client.post(f"/api/qa/turns/{tid}/feedback", json={"rating": "up"}, headers=_auth(_OTHER))
        assert r.status_code == 404, r.text
        assert "QA_TURN_NOT_FOUND" in r.text

    async def test_missing_turn_is_not_found(self, test_db):
        from fastapi.testclient import TestClient

        from app.main import app

        async with test_db() as db:
            await _seed_turn(db)

        with TestClient(app) as client:
            r = client.post("/api/qa/turns/nope/feedback", json={"rating": "up"}, headers=_auth(_ME))
        assert r.status_code == 404

    async def test_invalid_rating_is_rejected(self, test_db):
        from fastapi.testclient import TestClient

        from app.main import app

        async with test_db() as db:
            tid = await _seed_turn(db)

        with TestClient(app) as client:
            r = client.post(f"/api/qa/turns/{tid}/feedback", json={"rating": "meh"}, headers=_auth(_ME))
        assert r.status_code == 422


@pytest.mark.asyncio
class TestColumnsExist:
    async def test_migration_added_both_columns(self, test_db):
        """B2/B4 的列必须真的被迁移加上（模型加列 + 迁移漏了 = 上线即 500）"""
        async with test_db() as db:
            await _seed_turn(db)
            rows = (await db.execute(text("PRAGMA table_info(qa_turns)"))).all()
        cols = {r[1] for r in rows}
        assert "feedback" in cols, f"迁移没加 feedback（现有列：{sorted(cols)}）"
        assert "scope_json" in cols, f"迁移没加 scope_json（现有列：{sorted(cols)}）"
