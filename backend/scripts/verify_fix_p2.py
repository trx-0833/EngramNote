"""F-31 验证：版本唯一索引创建 + 模块导入"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from _tmpdb import bootstrap_temp_db, teardown_temp_db


async def main():
    from sqlalchemy import text
    import app.models  # noqa: F401
    from app import database as db_mod

    # 临时库引导（见 scripts/_tmpdb.py：直接重绑定 db_mod.engine 是无效的）
    db_path = await bootstrap_temp_db("ver_test")
    await db_mod.init_db()
    async with db_mod.get_engine().begin() as conn:
        idx = (await conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='index' AND name IN "
            "('uq_note_versions_note_version','uq_card_relations_pair_type')"
        ))).scalars().all()
        assert "uq_note_versions_note_version" in idx, f"版本唯一索引缺失: {idx}"
        assert "uq_card_relations_pair_type" in idx, f"关系唯一索引缺失: {idx}"
        print("[OK] 索引齐全:", sorted(idx))
    # 这两个 import 是**验证目标本身**（模块能否导入），不取别名会被 ruff 判为未使用
    from app.services.version_service import version_service  # noqa: F401
    from app.services.note_service import save_note_content  # noqa: F401
    print("[OK] version_service / note_service 导入正常")
    await teardown_temp_db(db_path)
    print("\n=== F-31 验证通过 ===")

asyncio.run(main())
