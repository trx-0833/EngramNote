"""F-01/F-07 行为验证脚本：独立临时库，不触碰真实数据。

验证点：
1. init_db 后 card_relations 唯一索引 uq_card_relations_pair_type 存在
2. PRAGMA foreign_keys 已开启
3. 唯一索引拒绝完全同键重复插入；方向相反/不同类型关系可共存
4. delete_note 完整清理（knowledge_cards 与 note_projects 无残留）
"""
import asyncio
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from sqlalchemy import text


async def main():
    tmpdir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "tmp_test")
    os.makedirs(tmpdir, exist_ok=True)
    db_path = os.path.join(tmpdir, "test.db")
    if os.path.exists(db_path):
        os.remove(db_path)

    # 覆盖数据库路径
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    from app.config import get_settings
    get_settings.cache_clear()
    import app.models  # noqa: F401 — 注册全部模型到 Base.metadata
    from app import database as db_mod

    db_mod.settings = get_settings()
    db_mod.database_url = db_mod.settings.get_database_url()
    db_mod._is_sqlite = db_mod.database_url.startswith("sqlite")
    db_mod.engine = db_mod.create_async_engine(db_mod.database_url, **{
        "echo": False,
        "connect_args": {"check_same_thread": False},
    })
    if db_mod._is_sqlite:
        db_mod.register_sqlite_pragmas(db_mod.engine)

    await db_mod.init_db()

    async with db_mod.async_session() as s:
        # 1. 唯一索引存在
        rows = (await s.execute(text(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='uq_card_relations_pair_type'"
        ))).scalars().all()
        assert rows, "唯一索引未创建"
        print("[OK] uq_card_relations_pair_type 索引存在")

        # 2. PRAGMA foreign_keys
        fk = (await s.execute(text("PRAGMA foreign_keys"))).scalar()
        assert fk == 1, f"foreign_keys 未开启: {fk}"
        print("[OK] PRAGMA foreign_keys = 1")

        # 3. 造数据（ORM 插入）
        from app.models.user import User as U
        from app.models.note import Note as N, SourceType, NoteStatus
        from app.models.knowledge_card import KnowledgeCard as KC, CardType
        from app.models.card_relation import CardRelation as CR, RelationType, RelationStatus

        s.add(U(id="u1", email="a@b.c", username="a", hashed_password="x"))
        await s.flush()
        s.add_all([
            N(id="n1", user_id="u1", title="n1", source_type=SourceType.pdf, status=NoteStatus.cleaned,
              file_size=1, original_file_path="/u1/n1.pdf", original_md_path="/u1/n1.md"),
            N(id="n2", user_id="u1", title="n2", source_type=SourceType.pdf, status=NoteStatus.cleaned,
              file_size=1, original_file_path="/u1/n2.pdf", original_md_path="/u1/n2.md"),
        ])
        await s.flush()
        s.add_all([
            KC(id="c1", user_id="u1", note_id="n1", title="t1", content="x", card_type=CardType.concept),
            KC(id="c2", user_id="u1", note_id="n1", title="t2", content="x", card_type=CardType.concept),
        ])
        await s.flush()
        s.add_all([
            CR(id="r1", user_id="u1", card_id_1="c1", card_id_2="c2", relation_type=RelationType.prerequisite, status=RelationStatus.confirmed),
        ])
        s.add(CR(id="r3", user_id="u1", card_id_1="c2", card_id_2="c1", relation_type=RelationType.prerequisite, status=RelationStatus.confirmed))
        s.add(CR(id="r4", user_id="u1", card_id_1="c1", card_id_2="c2", relation_type=RelationType.related, status=RelationStatus.confirmed))
        await s.commit()

        # 完全同键重复插入必须被唯一索引拒绝
        s.add(CR(id="r2", user_id="u1", card_id_1="c1", card_id_2="c2", relation_type=RelationType.prerequisite, status=RelationStatus.confirmed))
        try:
            await s.commit()
            raise AssertionError("完全同键重复插入未被唯一索引拒绝")
        except Exception as e:
            assert "unique" in str(e).lower(), f"预期唯一约束错误，实际: {e}"
            await s.rollback()
            print("[OK] 唯一索引拒绝完全同键重复插入（r1 vs r2）")

    # 再次执行 _migrate_sqlite（安全去重：无完全同键重复，应零删除）
    async with db_mod.engine.begin() as conn:
        await db_mod._migrate_sqlite(conn)

    async with db_mod.async_session() as s:
        rows = (await s.execute(text(
            "SELECT id FROM card_relations ORDER BY id"
        ))).scalars().all()
        assert rows == ["r1", "r3", "r4"], f"去重结果错误: {rows}"
        print(f"[OK] 安全去重：反向 r3/异型 r4 与 r1 全部保留 -> {rows}")

        # 4. delete_note 完整清理
        from app.models.project import Project as P
        s.add(P(id="p1", user_id="u1", name="proj1"))
        await s.commit()
        from app.models.note_project import NoteProject as NP
        s.add(NP(id="np1", note_id="n1", project_id="p1", user_id="u1"))
        await s.commit()

        from app.services.note_service import delete_note
        from app.models.note import Note as N2
        from sqlalchemy import select
        note_orm = (await s.execute(select(N2).where(N2.id == "n1"))).scalar_one()
        await delete_note(s, note_orm)
        await s.commit()

        remaining = (await s.execute(text(
            "SELECT COUNT(*) FROM knowledge_cards WHERE note_id='n1'"
        ))).scalar()
        assert remaining == 0, f"知识卡片未随笔记删除: 剩余 {remaining}"
        np_remaining = (await s.execute(text(
            "SELECT COUNT(*) FROM note_projects WHERE note_id='n1'"
        ))).scalar()
        assert np_remaining == 0, f"note_projects 孤儿残留: {np_remaining}"
        print("[OK] delete_note 完整清理：knowledge_cards 与 note_projects 均无残留")

    await db_mod.engine.dispose()
    shutil.rmtree(tmpdir, ignore_errors=True)
    print("\n=== F-01/F-07 全部验证通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
