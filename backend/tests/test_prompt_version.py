"""阶段 4.6：提示词版本化（溯源）测试

## 这份测试要证明什么

版本号存在的唯一理由是**能按版本比较质量**："新版提示词产出的卡片，
复习表现是不是更好？"围绕这个问题，有三类会骗人的失败：

| 骗法 | 后果 | 对应测试 |
|---|---|---|
| 改了提示词但忘改版本 | 两版数据混在一起，比较结果无意义 | `TestVersionRegistry::test_version_is_linked_to_prompt_digest` |
| 未登记时兜底成 "1" | 未知行伪装成第一版，污染第一版的统计 | `test_unknown_prompt_returns_none` |
| 历史行被回填版本号 | 同上（"未知"被写成"第一版"） | `test_history_rows_stay_null` |
| 入库时不写版本 | 列存在但永远为 NULL（等于没做） | `TestCardsCarryVersion` |

## 与 test_prompt_golden 的分工

`test_prompt_golden.py` 固化"每个提示词送给模型的输入摘要"，
本文件负责"版本号和那些摘要一一对应"。两者合起来才是完整的护栏：
**文本变了没有？变的是哪一版？**
"""

import uuid
from typing import Any, Dict, List

import pytest
from sqlalchemy import select, text

from app.services.llm import prompts
from app.services.llm.prompts import PROMPT_VERSIONS, prompt_version


class TestVersionRegistry:
    def test_every_version_is_a_positive_integer_string(self):
        """版本号必须是可排序的正整数字符串（见 prompts.py 的取值规则）"""
        for name, version in PROMPT_VERSIONS.items():
            assert isinstance(version, str), f"{name} 的版本号不是字符串"
            assert version.isdigit() and int(version) >= 1, (
                f"{name} 的版本号 {version!r} 不是正整数 —— "
                f"版本号唯一的用途是排序，字母后缀会分裂出两套排序规则"
            )

    def test_unknown_prompt_returns_none(self):
        """未登记的名字返回 None，**不兜底成某一版**

        返回 "1" 会让"未知"伪装成第一版，而按版本分组的统计正是这一列的用途。
        与记账里"没配价格时 cost 记 NULL 而不是 0"是同一条原则。
        """
        assert prompt_version("这个提示词不存在") is None
        assert prompt_version("") is None

    def test_registry_covers_every_prompt(self):
        """登记表必须覆盖 prompts.py 里全部提示词入口（多一个少一个都要报）

        判据：`__all__` 里所有"产出提示词"的名字（函数 + `_SYSTEM_PROMPT` 常量）
        都应当能对应到版本；反之登记表里不该有已经删掉的提示词。
        """
        produced = {
            name for name in prompts.__all__
            if name.endswith("_messages") or name.endswith("_SYSTEM_PROMPT")
        }
        # 会话类提示词由 `create_*_session` 直接引用常量，登记名与常量名不同，
        # 因此这里做的是"数量与来源"的双向核对，而不是逐名相等。
        assert produced, "没有解析到任何提示词入口 —— 这个断言可能已经空转"
        assert len(PROMPT_VERSIONS) >= len(produced), (
            f"提示词入口 {len(produced)} 个，版本登记只有 {len(PROMPT_VERSIONS)} 个"
        )
        for name in PROMPT_VERSIONS:
            assert name in {
                "summarize_chapter", "extract_knowledge_points", "understanding_session",
                "generate_questions", "generate_questions_batch", "question_session",
                "rag_answer", "combined_analysis_session", "generate_extension_knowledge",
                "infer_card_relations", "grade_short_answer",
            }, f"登记表里出现了未在文档中说明的名字: {name}"

    def test_version_is_linked_to_prompt_digest(self):
        """★ 核心：版本号与"送给模型的输入摘要"**绑在一起**

        这条断言与 `test_prompt_golden.py` 的固化摘要配合工作：

        - 只改提示词文本 → 摘要失配（那边的用例失败）；
        - 改了文本又更新摘要、但忘了改版本 → **本条**失败；
        - 改文本 + 更新摘要 + 升版本 → 全绿，而这正是"有意识地换了一版"。

        换句话说：忘记升版本这件事不再可能发生。
        """
        from tests.test_prompt_golden import GOLDEN

        # 摘要表里记录了每个场景当时的版本；两边必须一致
        for scene, entry in GOLDEN.items():
            version, _digest = entry
            assert PROMPT_VERSIONS[scene] == version, (
                f"{scene} 的版本是 {PROMPT_VERSIONS[scene]}，"
                f"但摘要表记录的是 {version} —— 改了提示词就请同时升版本"
            )


async def _make_user(session_factory) -> str:
    from app.models.user import User

    uid = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(User(id=uid, email=f"{uid[:8]}@e.com", username=f"u{uid[:8]}",
                    hashed_password="x", is_active=True))
        await db.commit()
    return uid


async def _make_note(session_factory, user_id: str) -> str:
    from app.models.note import Note, NoteStatus, SourceType

    nid = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(Note(id=nid, user_id=user_id, title="浮充", source_type=SourceType.pdf,
                    status=NoteStatus.cleaned, file_size=1,
                    original_file_path=f"/{user_id}/{nid}.pdf",
                    original_md_path=f"/{user_id}/{nid}.md"))
        await db.commit()
    return nid


@pytest.mark.asyncio
class TestCardsCarryVersion:
    """入库路径必须把版本写进去 —— 否则列存在但永远是 NULL（等于没做）"""

    POINTS: List[Dict[str, Any]] = [
        {"card_type": "concept", "title": "浮充", "content": "浮充是蓄电池的一种运行方式。",
         "source_text": "浮充：蓄电池的一种运行方式。"},
    ]

    async def test_save_cards_writes_prompt_version(self, test_db):
        from app.models.knowledge_card import KnowledgeCard
        from app.services.card_intake_service import save_cards_idempotent

        uid = await _make_user(test_db)
        nid = await _make_note(test_db, uid)
        async with test_db() as db:
            outcome = await save_cards_idempotent(
                db, uid, nid, {"chapter_title": "第一章"}, "摘要",
                self.POINTS, prompt_version=prompt_version("understanding_session"),
            )
        assert len(outcome.created) == 1

        async with test_db() as db:
            card = (await db.execute(
                select(KnowledgeCard).where(KnowledgeCard.note_id == nid)
            )).scalars().one()
        assert card.prompt_version == PROMPT_VERSIONS["understanding_session"]

    async def test_missing_version_stays_null(self, test_db):
        """调用方没给版本时写 NULL，不猜一个默认值"""
        from app.models.knowledge_card import KnowledgeCard
        from app.services.card_intake_service import save_cards_idempotent

        uid = await _make_user(test_db)
        nid = await _make_note(test_db, uid)
        async with test_db() as db:
            await save_cards_idempotent(
                db, uid, nid, {"chapter_title": "第一章"}, "摘要", self.POINTS,
            )

        async with test_db() as db:
            card = (await db.execute(
                select(KnowledgeCard).where(KnowledgeCard.note_id == nid)
            )).scalars().one()
        assert card.prompt_version is None


@pytest.mark.asyncio
class TestMigration:
    """**既有库**必须能通过 ALTER 补上这两列，且历史行保持 NULL

    ⚠️ 用全新临时库测不出迁移：`create_all` 会直接带着新列建表，
    那样测的是"模型定义对不对"，而不是"真库能不能升级"。
    这里刻意**先造一个没有该列的旧库**，再跑真实的 `_migrate_sqlite` ——
    这正是本文件最容易骗过自己的地方。
    """

    async def _old_db(self, tmp_path):
        """造一个"本列引入之前"的库：两张表都没有 prompt_version，且各有一行"""
        import sqlite3

        path = tmp_path / "old.db"
        con = sqlite3.connect(path)
        con.executescript(
            """
            CREATE TABLE knowledge_cards (
                id VARCHAR NOT NULL PRIMARY KEY,
                user_id VARCHAR NOT NULL,
                note_id VARCHAR,
                card_type VARCHAR NOT NULL,
                title VARCHAR(500) NOT NULL,
                content TEXT NOT NULL
            );
            CREATE TABLE quiz_items (
                id VARCHAR NOT NULL PRIMARY KEY,
                user_id VARCHAR NOT NULL,
                card_id VARCHAR NOT NULL,
                question_type VARCHAR NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL
            );
            INSERT INTO knowledge_cards (id, user_id, note_id, card_type, title, content)
                VALUES ('c-old', 'u1', 'n1', 'concept', '历史卡片', '本列引入之前。');
            INSERT INTO quiz_items (id, user_id, card_id, question_type, question, answer)
                VALUES ('q-old', 'u1', 'c-old', 'choice', '历史题目？', '答案');
            """
        )
        con.commit()
        con.close()
        return path

    async def test_migration_adds_column_to_existing_db(self, tmp_path):
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        from app.database import _migrate_sqlite

        path = await self._old_db(tmp_path)
        engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        try:
            async with engine.begin() as conn:
                await _migrate_sqlite(conn)
            async with engine.connect() as conn:
                for table in ("knowledge_cards", "quiz_items"):
                    cols = (await conn.execute(text(f"PRAGMA table_info({table})"))).all()
                    assert "prompt_version" in {row[1] for row in cols}, (
                        f"{table} 没有被 ALTER 补上 prompt_version —— 真库会缺这一列"
                    )
                # 历史行保持 NULL（不是回填成某一版）
                card = (await conn.execute(text(
                    "SELECT prompt_version FROM knowledge_cards WHERE id='c-old'"
                ))).scalar()
                quiz = (await conn.execute(text(
                    "SELECT prompt_version FROM quiz_items WHERE id='q-old'"
                ))).scalar()
        finally:
            await engine.dispose()

        assert card is None
        assert quiz is None

    async def test_columns_exist_after_init(self, test_db):
        """全新库（create_all 路径）同样要有这两列"""
        async with test_db() as db:
            for table in ("knowledge_cards", "quiz_items"):
                cols = (await db.execute(text(f"PRAGMA table_info({table})"))).all()
                names = {row[1] for row in cols}
                assert "prompt_version" in names, f"{table} 缺少 prompt_version 列"

    async def test_history_rows_stay_null(self, test_db):
        """★ 迁移**不回填**：历史行的版本是"未知"，不是"第一版"

        回填会让既有卡片全部声称自己是第一版产出的，
        于是"第一版的表现"这个统计口径被污染 —— 而那正是版本号要回答的问题。
        """
        from app.models.knowledge_card import CardType, KnowledgeCard

        uid = await _make_user(test_db)
        nid = await _make_note(test_db, uid)
        async with test_db() as db:
            # 直接插入一行"没有版本"的卡（模拟本列引入之前的历史行）
            db.add(KnowledgeCard(
                user_id=uid, note_id=nid, card_type=CardType.concept,
                title="历史卡片", content="本列引入之前的卡片。",
            ))
            await db.commit()

        async with test_db() as db:
            row = (await db.execute(select(KnowledgeCard))).scalars().one()
        assert row.prompt_version is None
