"""F-31 验证：版本唯一索引创建 + 模块导入"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

async def main():
    from sqlalchemy import text
    import app.models  # noqa: F401
    from app import database as db_mod

    p = os.path.abspath("backend/data/tmp_test/ver_test.db")
    if os.path.exists(p):
        os.remove(p)
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{p}"
    from app.config import get_settings
    get_settings.cache_clear()
    db_mod.settings = get_settings()
    db_mod.database_url = db_mod.settings.get_database_url()
    db_mod._is_sqlite = True
    db_mod.engine = db_mod.create_async_engine(
        db_mod.database_url, connect_args={"check_same_thread": False}, echo=False
    )
    db_mod.register_sqlite_pragmas(db_mod.engine)
    await db_mod.init_db()
    async with db_mod.engine.begin() as conn:
        idx = (await conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='index' AND name IN "
            "('uq_note_versions_note_version','uq_card_relations_pair_type')"
        ))).scalars().all()
        assert "uq_note_versions_note_version" in idx, f"版本唯一索引缺失: {idx}"
        assert "uq_card_relations_pair_type" in idx, f"关系唯一索引缺失: {idx}"
        print("[OK] 索引齐全:", sorted(idx))
    from app.services.version_service import version_service
    from app.services.note_service import save_note_content
    print("[OK] version_service / note_service 导入正常")
    await db_mod.engine.dispose()
    print("\n=== F-31 验证通过 ===")

asyncio.run(main())
