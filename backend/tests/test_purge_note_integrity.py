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
