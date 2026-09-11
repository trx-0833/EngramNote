"""
物理删除笔记的外键完整性测试（overhaul-plan 症状 M-4 / 阶段 1.13）

## 缺陷（文档 §2.6 M-4，本轮重新确认仍然存在）

`purge_note` 只处理了"待删卡片**作为父**"的 `parent_card_id` 引用：

    UPDATE knowledge_cards SET parent_card_id = NULL
     WHERE parent_card_id IN (待删卡片)

却没处理"待删卡片**作为子**"—— 即**其他笔记的卡片把 parent_card_id 指向
本笔记的卡片**。"拓展卡片"功能正是这么用的。而
`knowledge_cards.parent_card_id` 与 `quiz_items.card_id` 都**没有 ondelete**
（默认 NO ACTION），于是 DELETE 抛 `FOREIGN KEY constraint failed`。

后果是**用户永远删不掉该笔记**，且删除前 `_abort_processing` 已把状态
commit 成 failed，**无法恢复**。

审计时线上 `parent_card_id` 全为 NULL，所以一直没暴露 ——
只要用户用过一次"拓展卡片"就会触发。

## 为什么用真实 SQLite 约束做测试

fixture 建的临时库通过 `create_all` 生成，并注册了
`PRAGMA foreign_keys=ON`（见 database.register_sqlite_pragmas），
因此外键约束是**真的在生效**的，不是 mock。
"""

import uuid

import pytest
from sqlalchemy import select

from app.models.chunk import Chunk
from app.models.knowledge_card import CardType, KnowledgeCard
from app.models.note import Note, NoteStatus, SourceType
from app.models.quiz_item import QuestionType, QuizItem
from app.models.user import User
from app.services.note_service import purge_note


async def _make_user(session_factory) -> str:
    uid = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(User(
            id=uid, email=f"{uid[:8]}@example.com", username=f"u{uid[:8]}",
            hashed_password="x", is_active=True,
        ))
        await db.commit()
    return uid


async def _make_note(session_factory, user_id: str, title: str = "n") -> str:
    nid = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(Note(
            id=nid, user_id=user_id, title=title,
            source_type=SourceType.pdf, status=NoteStatus.cleaned,
        ))
        await db.commit()
    return nid


async def _make_card(
    session_factory, user_id: str, note_id: str | None,
    *, parent_card_id: str | None = None, title: str = "c",
) -> str:
    cid = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(KnowledgeCard(
            id=cid, user_id=user_id, note_id=note_id,
            card_type=CardType.concept, title=title, content="x",
            parent_card_id=parent_card_id,
        ))
        await db.commit()
    return cid


async def _make_quiz(session_factory, user_id: str, card_id: str, note_id: str | None) -> str:
    qid = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(QuizItem(
            id=qid, user_id=user_id, note_id=note_id, card_id=card_id,
            question="q", answer="a", question_type=QuestionType.short_answer,
        ))
        await db.commit()
    return qid


async def _make_chunks(
    session_factory, user_id: str, note_id: str, count: int = 2,
) -> None:
    """写入若干 chunk（阶段 2.2′ 引入的表）

    偏移刻意自洽（`char_end - char_start == len(content)`），
    与真实写入路径（`chunk_service.index_note_chunks`）保持一致。
    """
    async with session_factory() as db:
        for i in range(count):
            content = f"第{i}段内容"
            db.add(Chunk(
                user_id=user_id, note_id=note_id, index=i,
                content=content, char_start=i * 100, char_end=i * 100 + len(content),
                heading_path=f"第一章 > 1.{i}", line_start=i * 5, line_end=i * 5 + 3,
                char_count=len(content), grams=content,
                source_md_path="x.md", content_hash="h", has_embedding=False,
            ))
        await db.commit()


async def _purge(session_factory, note_id: str) -> None:
    async with session_factory() as db:
        note = (await db.execute(select(Note).where(Note.id == note_id))).scalars().first()
        assert note is not None
        await purge_note(db, note)
        await db.commit()


async def _exists(session_factory, model, row_id: str) -> bool:
    async with session_factory() as db:
        return (await db.execute(
            select(model.id).where(model.id == row_id)
        )).scalars().first() is not None


@pytest.mark.asyncio
class TestPurgeNoteForeignKeyIntegrity:
    """`purge_note` 必须在有跨笔记引用时也能成功"""

    async def test_baseline_purge_without_references(self, test_db):
        """基线：没有任何外部引用时，purge 正常完成

        先跑这条，确保后面的失败确实是"跨笔记引用"导致的，
        而不是 purge 本身坏了。
        """
        uid = await _make_user(test_db)
        note_id = await _make_note(test_db, uid)
        card_id = await _make_card(test_db, uid, note_id)
        await _make_quiz(test_db, uid, card_id, note_id)

        await _purge(test_db, note_id)

        assert not await _exists(test_db, Note, note_id)
        assert not await _exists(test_db, KnowledgeCard, card_id)

    async def test_other_note_card_pointing_at_ours(self, test_db):
        """跨笔记 parent_card_id 引用：purge 必须成功

        场景：在笔记 B 上基于笔记 A 的卡片生成"拓展卡片"，
        拓展卡片的 `parent_card_id` 指向 A 的卡片。删除 A 时若不清掉这个引用，
        DELETE 会抛 `FOREIGN KEY constraint failed`。

        ## 注意：文档 §2.6 M-4 对此的描述不准确

        文档说 `purge_note` "只处理了待删卡片**作为父**的引用，没处理作为子"，
        暗示跨笔记引用会漏。**实测不成立**：第 607 行的
        `WHERE parent_card_id IN (待删卡片)` 匹配的正是"**父**是被删卡片"的行，
        跨笔记的拓展卡片恰好命中这一条件，因此会被正确置空。

        本测试因此不是"复现缺陷"，而是**锁住这个行为** —— 因为它依赖一个
        不显眼的事实（`WHERE parent_card_id IN (...)` 的作用域是全表而非本笔记），
        将来若有人给这个 UPDATE 加上 `note_id == note_id` 限定，就会真的引入 M-4。

        为排除"测试前提没成立"的可能，这里先断言引用确实建立成功。
        """
        uid = await _make_user(test_db)
        note_a = await _make_note(test_db, uid, "A")
        note_b = await _make_note(test_db, uid, "B")

        parent_card = await _make_card(test_db, uid, note_a, title="A 的卡片")
        child_card = await _make_card(
            test_db, uid, note_b, parent_card_id=parent_card, title="B 的拓展卡片",
        )

        # 前提校验：跨笔记引用确实建立了
        async with test_db() as db:
            linked = (await db.execute(
                select(KnowledgeCard.parent_card_id).where(KnowledgeCard.id == child_card)
            )).scalar()
        assert linked == parent_card, (
            f"测试前提未成立：B 的卡片没有指向 A 的卡片（实际 parent={linked}）"
        )

        await _purge(test_db, note_a)

        assert not await _exists(test_db, Note, note_a)
        assert not await _exists(test_db, KnowledgeCard, parent_card)
        # B 的拓展卡片应保留（悬挂），只是父引用被清空
        assert await _exists(test_db, KnowledgeCard, child_card)
        async with test_db() as db:
            after = (await db.execute(
                select(KnowledgeCard.parent_card_id).where(KnowledgeCard.id == child_card)
            )).scalar()
        assert after is None, "被删卡片的引用未被置空 —— 留下了悬挂外键"

    async def test_other_note_quiz_pointing_at_our_card(self, test_db):
        """跨笔记 quiz_items.card_id 引用：purge 必须成功

        **这是文档 §2.6 M-4 未记录的第二条路径。** 文档只提到了
        `parent_card_id`，但 `quiz_items.card_id` 同样是
        `ForeignKey("knowledge_cards.id")` 且**无 ondelete**，
        而且它是 **NOT NULL**，所以不能像 `parent_card_id` 那样"置 NULL 悬挂"，
        只能在删除卡片前把这类题目一并删除。

        `purge_note` 第 585-601 行的选取范围是
        `QuizItem.card_id.in_(card_ids) OR (note_id == note_id AND card_id IS NULL)`
        —— 第一个条件**不带 note_id 限定**，因此跨笔记的题目也被覆盖。
        本测试锁住这一点。
        """
        uid = await _make_user(test_db)
        note_a = await _make_note(test_db, uid, "A")
        note_b = await _make_note(test_db, uid, "B")

        card_a = await _make_card(test_db, uid, note_a, title="A 的卡片")
        # B 的题目挂在 A 的卡片上（跨笔记引用）
        quiz_b = await _make_quiz(test_db, uid, card_a, note_b)

        # 前提校验
        async with test_db() as db:
            quiz = (await db.execute(
                select(QuizItem).where(QuizItem.id == quiz_b)
            )).scalars().first()
        assert quiz is not None and quiz.card_id == card_a and quiz.note_id == note_b, (
            "测试前提未成立：题目没有形成「B 的题目挂 A 的卡片」的跨笔记引用"
        )

        await _purge(test_db, note_a)

        assert not await _exists(test_db, Note, note_a)
        assert not await _exists(test_db, KnowledgeCard, card_a)
        # 该题目必须被一并删除（card_id 是 NOT NULL，无法悬挂）
        assert not await _exists(test_db, QuizItem, quiz_b), (
            "指向被删卡片的跨笔记题目未被清理 —— 卡片删除会外键违约"
        )

    async def test_self_parent_card_is_detached(self, test_db):
        """同笔记内的父子卡片引用（已有逻辑覆盖，防回归）"""
        uid = await _make_user(test_db)
        note_id = await _make_note(test_db, uid)
        parent = await _make_card(test_db, uid, note_id, title="父")
        child = await _make_card(test_db, uid, note_id, parent_card_id=parent, title="子")

        await _purge(test_db, note_id)

        assert not await _exists(test_db, KnowledgeCard, parent)
        assert not await _exists(test_db, KnowledgeCard, child)

    async def test_purge_is_clean_when_card_already_dangling(self, test_db):
        """已被提升为独立节点（note_id=NULL）的卡片不受影响"""
        uid = await _make_user(test_db)
        note_id = await _make_note(test_db, uid)
        await _make_card(test_db, uid, note_id, title="普通")
        independent = await _make_card(test_db, uid, None, title="独立卡片")

        await _purge(test_db, note_id)

        assert not await _exists(test_db, Note, note_id)
        assert await _exists(test_db, KnowledgeCard, independent), (
            "独立卡片（note_id=NULL）被误删"
        )


@pytest.mark.asyncio
class TestPurgeNoteChunks:
    """`purge_note` 必须清理 `chunks`（阶段 2.2′ 引入的表）

    ## 这是一个真实发生过的回归

    `chunks.note_id` 的外键是 **NO ACTION**（不是 CASCADE），而
    `PRAGMA foreign_keys=ON` 已在每个连接上生效。引入 `chunks` 表时
    忘记在删除路径里处理它，于是**删除任何有 chunk 的笔记都会抛
    `IntegrityError: FOREIGN KEY constraint failed`** —— 而这恰好发生在
    本轮刚上线的新表上，属"新功能把老功能弄坏"的典型。

    与 M-4 的区别值得记下：M-4 经复核**不存在**（那条 UPDATE 没有 note_id
    限定，跨笔记引用已被覆盖）；这一条是**真实存在**的，由本轮引入并由
    测试当场锁住。

    模型上加了 `ondelete="CASCADE"` 作为纵深防御，但**不能依赖它**：
    已有库的旧表不会因为模型改了 ondelete 就重建，`CREATE TABLE` 里的
    ON DELETE 子句才是实际生效的那个。所以断言的是"purge 能成功且不留
    孤儿 chunk"，而不是"外键声明写了 CASCADE"。
    """

    async def test_purge_succeeds_with_chunks(self, test_db):
        """有 chunk 的笔记必须能被删除，且不留孤儿 chunk"""
        uid = await _make_user(test_db)
        note_id = await _make_note(test_db, uid)
        await _make_chunks(test_db, uid, note_id, count=3)

        # 前提校验：确实写进去了（否则下面的断言是空转）
        async with test_db() as db:
            before = len((await db.execute(
                select(Chunk.id).where(Chunk.note_id == note_id)
            )).scalars().all())
        assert before == 3, f"前提未成立：只写入了 {before} 个 chunk"

        await _purge(test_db, note_id)

        assert not await _exists(test_db, Note, note_id)
        async with test_db() as db:
            orphans = len((await db.execute(
                select(Chunk.id).where(Chunk.note_id == note_id)
            )).scalars().all())
        assert orphans == 0, f"删除笔记后残留 {orphans} 个孤儿 chunk"

    async def test_fk_is_actually_enforced(self, test_db):
        """**对照实验**：确认外键真的在生效

        若 FK 没有启用，"忘记删 chunks"这种缺陷**不会报错**，
        上一条测试也就失去了意义。这里直接验证删笔记（不删 chunk）
        会被数据库拒绝 —— 与 M-4 那组测试用同一个手法：
        先证明约束是活的，再证明代码满足了它。
        """
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError

        uid = await _make_user(test_db)
        note_id = await _make_note(test_db, uid)
        await _make_chunks(test_db, uid, note_id, count=1)

        async with test_db() as db:
            fk_on = (await db.execute(text("PRAGMA foreign_keys"))).scalar()
            assert fk_on == 1, "测试库未启用外键，本组测试无法验证真实约束"

        async with test_db() as db:
            with pytest.raises(IntegrityError):
                await db.execute(
                    text("DELETE FROM notes WHERE id = :i"), {"i": note_id}
                )
                await db.commit()

    async def test_chunks_of_other_notes_untouched(self, test_db):
        """只删目标笔记的 chunk，不能牵连别人的"""
        uid = await _make_user(test_db)
        note_a = await _make_note(test_db, uid, title="A")
        note_b = await _make_note(test_db, uid, title="B")
        await _make_chunks(test_db, uid, note_a, count=2)
        await _make_chunks(test_db, uid, note_b, count=2)

        await _purge(test_db, note_a)

        async with test_db() as db:
            left = len((await db.execute(
                select(Chunk.id).where(Chunk.note_id == note_b)
            )).scalars().all())
        assert left == 2, f"误删了其他笔记的 chunk（剩 {left}）"
