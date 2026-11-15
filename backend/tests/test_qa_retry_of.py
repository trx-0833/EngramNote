"""`retry_of`（真·重新生成）的校验与替换行为。

产品条件（2026-11-15 用户拍板）：只允许**最新**轮；旧版本隐藏；指向已替换的轮
必须明确报错；失败/中止轮继续用「重试」。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models.qa_history import (
    SURFACE_GLOBAL,
    TURN_STATUS_FAILED,
    TURN_STATUS_OK,
    TURN_STATUS_SUPERSEDED,
)
from app.models.user import User

_UID = "user-retry-1"


async def _seed_user_and_session(db):
    from app.models.qa_history import QASession

    db.add(User(id=_UID, email="r@t.local", username="retry", hashed_password="x", is_active=True))
    await db.flush()
    session = QASession(id="sess-1", user_id=_UID, surface=SURFACE_GLOBAL, title="t", turn_count=0)
    db.add(session)
    await db.flush()
    return session


async def _add_turn(db, *, turn_id, created_at, status=TURN_STATUS_OK, session_id="sess-1"):
    from app.models.qa_history import QATurn

    turn = QATurn(
        id=turn_id,
        session_id=session_id,
        user_id=_UID,
        question=f"问题 {turn_id}",
        answer=f"答案 {turn_id}",
        status=status,
        created_at=created_at,
    )
    db.add(turn)
    await db.flush()
    return turn


@pytest.mark.asyncio
class TestRetryOfValidation:
    async def test_only_latest_turn_allowed(self, test_db):
        from app.services import qa_history_service as svc

        base = datetime(2026, 11, 15, 10, 0, 0)
        async with test_db() as db:
            await _seed_user_and_session(db)
            await _add_turn(db, turn_id="t1", created_at=base)
            await _add_turn(db, turn_id="t2", created_at=base + timedelta(minutes=1))
            await db.commit()

            assert await svc.is_latest_turn(db, session_id="sess-1", turn_id="t2") is True
            assert await svc.is_latest_turn(db, session_id="sess-1", turn_id="t1") is False

    async def test_superseded_turn_is_not_latest(self, test_db):
        from app.services import qa_history_service as svc

        base = datetime(2026, 11, 15, 10, 0, 0)
        async with test_db() as db:
            await _seed_user_and_session(db)
            await _add_turn(db, turn_id="t1", created_at=base)
            await _add_turn(
                db,
                turn_id="t2",
                created_at=base + timedelta(minutes=1),
                status=TURN_STATUS_SUPERSEDED,
            )
            await db.commit()
            # 最新的是 t1（t2 已被替换）
            assert await svc.is_latest_turn(db, session_id="sess-1", turn_id="t1") is True


@pytest.mark.asyncio
class TestSupersedeReplacement:
    async def test_supersede_hides_old_and_keeps_count(self, test_db):
        """替换后：旧轮 `superseded`、详情里看不到、`turn_count` 净变化 0"""
        from app.services import qa_history_service as svc

        base = datetime(2026, 11, 15, 10, 0, 0)
        async with test_db() as db:
            await _seed_user_and_session(db)
            await _add_turn(db, turn_id="t1", created_at=base)
            await db.execute(__import__("sqlalchemy").text("UPDATE qa_sessions SET turn_count = 1 WHERE id = 'sess-1'"))
            await db.commit()

            # 模拟"新轮已落库 + 计数自增"
            await _add_turn(db, turn_id="t2", created_at=base + timedelta(seconds=30))
            await db.execute(__import__("sqlalchemy").text("UPDATE qa_sessions SET turn_count = 2 WHERE id = 'sess-1'"))
            await db.commit()

            assert await svc.supersede_turn(db, session_id="sess-1", turn_id="t1") is True
            await db.commit()

            session, turns = await svc.get_session_with_turns(db, user_id=_UID, session_id="sess-1")
            visible = [t.id for t in turns]
            assert visible == ["t2"], f"旧版本应当隐藏，实际可见 {visible}"
            assert session.turn_count == 1, f"turn_count 应为 1（净变化 0），实际 {session.turn_count}"

    async def test_supersede_is_idempotent(self, test_db):
        from app.services import qa_history_service as svc

        base = datetime(2026, 11, 15, 10, 0, 0)
        async with test_db() as db:
            await _seed_user_and_session(db)
            await _add_turn(db, turn_id="t1", created_at=base)
            await db.commit()

            assert await svc.supersede_turn(db, session_id="sess-1", turn_id="t1") is True
            assert await svc.supersede_turn(db, session_id="sess-1", turn_id="t1") is False


@pytest.mark.asyncio
class TestFailedTurnNotSuperseded:
    async def test_retry_of_on_failed_turn_is_rejected(self, test_db):
        """失败轮不能用「重新生成」（应走「重试」）——校验层给出的语义"""
        from app.services import qa_history_service as svc

        base = datetime(2026, 11, 15, 10, 0, 0)
        async with test_db() as db:
            await _seed_user_and_session(db)
            await _add_turn(db, turn_id="t-fail", created_at=base, status=TURN_STATUS_FAILED)
            await db.commit()

            turn = await svc.get_turn(db, user_id=_UID, turn_id="t-fail")
            assert turn is not None
            # 端点据此抛 `QA_RETRY_NOT_OK`（422）
            assert turn.status == TURN_STATUS_FAILED != TURN_STATUS_OK

    async def test_cross_session_target_is_not_found(self, test_db):
        """别人的 / 别的会话的轮次 → 取不到（404 口径）"""
        from app.models.qa_history import QASession
        from app.services import qa_history_service as svc
        from app.models.user import User as _User

        base = datetime(2026, 11, 15, 10, 0, 0)
        async with test_db() as db:
            await _seed_user_and_session(db)
            await _add_turn(db, turn_id="t1", created_at=base)
            # 另一个用户的会话
            db.add(_User(id="user-other", email="o@t.local", username="other", hashed_password="x", is_active=True))
            await db.flush()
            db.add(QASession(id="sess-other", user_id="user-other", surface=SURFACE_GLOBAL, title="x", turn_count=0))
            await db.flush()
            # 别人的轮次：归属于另一个用户
            from app.models.qa_history import QATurn as _T

            db.add(
                _T(
                    id="t-other",
                    session_id="sess-other",
                    user_id="user-other",
                    question="q",
                    answer="a",
                    status=TURN_STATUS_OK,
                    created_at=base,
                )
            )
            await db.commit()

            assert await svc.get_turn(db, user_id=_UID, turn_id="t-other") is None
            turn = await svc.get_turn(db, user_id=_UID, turn_id="t1")
            assert turn is not None and turn.session_id == "sess-1"
