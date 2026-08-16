"""F-09 验证：goal scope 归属校验（IDOR 拒绝）与进度不渗漏"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

async def main():
    from fastapi import HTTPException
    import app.models  # noqa: F401
    from app import database as db_mod

    p = os.path.abspath("backend/data/tmp_test/goal_test.db")
    if os.path.exists(p):
        os.remove(p)
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{p}"
    from app.config import get_settings
    get_settings.cache_clear()
    db_mod.settings = get_settings()
    db_mod.database_url = db_mod.settings.get_database_url()
    db_mod._is_sqlite = db_mod.database_url.startswith("sqlite")
    db_mod.engine = db_mod.create_async_engine(
        db_mod.database_url, connect_args={"check_same_thread": False}, echo=False
    )
    if db_mod._is_sqlite:
        db_mod.register_sqlite_pragmas(db_mod.engine)
    await db_mod.init_db()

    from app.models.user import User as U
    from app.models.note import Note as N, SourceType, NoteStatus
    from app.models.folder import Folder as F
    from app.models.knowledge_card import KnowledgeCard as KC, CardType
    from app.services.goal_service import goal_service

    async with db_mod.async_session() as s:
        s.add_all([
            U(id="u1", email="a@x.c", username="a", hashed_password="x"),
            U(id="u2", email="b@x.c", username="b", hashed_password="x"),
        ])
        await s.flush()
        s.add_all([
            N(id="n1", user_id="u1", title="n1", source_type=SourceType.pdf, status=NoteStatus.cleaned,
              file_size=1, original_file_path="/u1/n1.pdf", original_md_path="/u1/n1.md"),
            N(id="n2", user_id="u2", title="n2", source_type=SourceType.pdf, status=NoteStatus.cleaned,
              file_size=1, original_file_path="/u2/n2.pdf", original_md_path="/u2/n2.md"),
        ])
        await s.flush()
        from datetime import datetime, timezone
        s.add_all([
            F(id="f1", user_id="u1", name="f1", folder_date=datetime.now(timezone.utc)),
            F(id="f2", user_id="u2", name="f2", folder_date=datetime.now(timezone.utc)),
        ])
        await s.commit()

        # 1. 合法 scope -> 通过
        class Req:
            name = "目标1"
            type = None
            scope_notes = ["n1"]
            scope_folders = ["f1"]
            target_mastery = 80.0
            deadline = None
        goal = await goal_service.create_goal("u1", Req(), s)
        print("[OK] 合法 scope 创建成功:", goal.id)

        # 2. 越权 scope_notes -> 拒绝
        class ReqBad:
            name = "目标2"
            type = None
            scope_notes = ["n2"]
            scope_folders = []
            target_mastery = 80.0
            deadline = None
        try:
            await goal_service.create_goal("u1", ReqBad(), s)
            raise AssertionError("越权 scope_notes 未被拒绝")
        except HTTPException as e:
            assert e.status_code == 400
            print("[OK] 越权 scope_notes 被拒绝:", e.detail)

        # 3. 越权 scope_folders -> 拒绝
        class ReqBad2:
            name = "目标3"
            type = None
            scope_notes = []
            scope_folders = ["f2"]
            target_mastery = 80.0
            deadline = None
        try:
            await goal_service.create_goal("u1", ReqBad2(), s)
            raise AssertionError("越权 scope_folders 未被拒绝")
        except HTTPException as e:
            assert e.status_code == 400
            print("[OK] 越权 scope_folders 被拒绝:", e.detail)

        # 4. 进度不渗漏
        s.add_all([
            KC(id="c1", user_id="u1", note_id="n1", title="t1", content="x",
               card_type=CardType.concept, mastery_level=50.0),
            KC(id="c2", user_id="u2", note_id="n2", title="t2", content="x",
               card_type=CardType.concept, mastery_level=90.0),
        ])
        await s.commit()
        progress = await goal_service.get_goal_progress(goal.id, "u1", s)
        assert progress["avg_mastery"] == 50.0, f"进度渗漏: {progress}"
        print("[OK] 进度统计仅含本人卡片:", progress["avg_mastery"])

    await db_mod.engine.dispose()
    print("\n=== F-09 全部验证通过 ===")

asyncio.run(main())
