# -*- coding: utf-8 -*-
"""
回收站过滤（`Note.not_trashed`）—— 一个**几乎恒真**的条件
=============================================================

## 这个 bug 是怎么发现的

做 A1 P0-3（疑似重复卡片可见化）时，我给一条用例写的是
"回收站里的卡不参与" —— 它**红了**：把 n2 移进回收站之后，n2 的卡片
照旧被查出来。追下去发现根因不在新代码里，而在 `Note.not_trashed`。

## 根因：`.correlate(None)` 把外层的表加进了子查询

改造前生成的 SQL：

```sql
WHERE t.note_id IS NULL OR (EXISTS (
    SELECT notes.id FROM notes, t          -- ← t 被当成了子查询自己的表
    WHERE notes.id = t.note_id AND notes.trashed_at IS NULL))
```

`correlate(None)` 关掉了**全部**关联，于是 SQLAlchemy 把外层的 `t`
也加进子查询的 FROM。那条 `notes.id = t.note_id` 于是变成
"notes 与**子查询自己那份 t 的任意一行**相等" —— 与外层的这一行无关。
后果：**只要库里存在任何一篇未回收的笔记**，EXISTS 对每一行都为真。

## 为什么它值得一个专门的测试文件

这个条件被 **20 处**调用（`graph_service` / `knowledge` / `understanding` /
`goal_service`）。它恒真意味着"回收站里的东西到处都能被看见" ——
而那种错误**不会报错、只会多给数据**，所以没有任何测试红过。

判据必须落在**"回收站里那篇笔记的卡不出现、未回收那篇的卡出现"**这一对上：
只看"未回收的卡出现了"是**恒真断言**，旧实现也能通过。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


async def _seed_two_notes(test_db):
    """建两篇笔记 + 各一张卡（n1 未回收、n2 待回收）"""
    from app.models.chunk import pack_vector
    from app.models.knowledge_card import CardType, KnowledgeCard
    from app.models.note import Note, NoteStatus, SourceType
    from app.models.user import User

    async with test_db() as session:
        session.add(User(id="u1", email="a@x.c", username="a", hashed_password="x"))
        await session.flush()
        for nid in ("n1", "n2"):
            session.add(Note(
                id=nid, user_id="u1", title=nid, source_type=SourceType.pdf,
                status=NoteStatus.cleaned, file_size=1,
                original_file_path=f"/u1/{nid}.pdf", original_md_path=f"/u1/{nid}.md",
            ))
        await session.flush()
        for cid, nid in (("c1", "n1"), ("c2", "n2")):
            session.add(KnowledgeCard(
                id=cid, user_id="u1", note_id=nid, card_type=CardType.concept,
                title=f"卡{cid}", content="内容", embedding=pack_vector([1.0, 0.0]),
                embedding_dim=2, embedding_model="m", has_embedding=True,
            ))
        await session.commit()


async def _trash(test_db, note_id: str):
    from sqlalchemy import select

    from app.models.note import Note

    async with test_db() as session:
        note = (await session.execute(select(Note).where(Note.id == note_id))).scalar_one()
        note.trashed_at = datetime.now(timezone.utc)
        await session.commit()


async def _visible_card_ids(test_db) -> set:
    """用 `not_trashed` 筛一遍卡片，返回可见的 id 集合"""
    from sqlalchemy import select

    from app.models.knowledge_card import KnowledgeCard
    from app.models.note import Note

    async with test_db() as session:
        rows = await session.execute(
            select(KnowledgeCard.id).where(Note.not_trashed(KnowledgeCard.note_id))
        )
        return set(rows.scalars().all())


class TestNotTrashed:
    @pytest.mark.asyncio
    async def test_nothing_trashed_shows_everything(self, test_db):
        await _seed_two_notes(test_db)
        assert await _visible_card_ids(test_db) == {"c1", "c2"}

    @pytest.mark.asyncio
    async def test_trashed_note_cards_disappear(self, test_db):
        """★★ 核心判据：回收站里那篇的卡**不出现**，未回收那篇的**仍出现**

        ⚠️ 两个方向都要断言：
        - 只断言"c2 不出现"—— 若实现把**所有**卡都过滤掉也会绿；
        - 只断言"c1 出现"—— 那正是旧实现（恒真）也能通过的恒真断言。
        """
        await _seed_two_notes(test_db)
        await _trash(test_db, "n2")

        visible = await _visible_card_ids(test_db)
        assert "c2" not in visible, (
            "回收站里的笔记，它的卡片仍然可见 —— `not_trashed` 没有按行的 note_id 过滤"
        )
        assert "c1" in visible, "未回收那篇的卡片被误伤了"

    @pytest.mark.asyncio
    async def test_all_notes_trashed_hides_everything(self, test_db):
        """★ 边界：**所有**笔记都进回收站时，一张卡都不该可见

        这条正是旧实现最容易露馅的地方：恒真的条件在这种情况下仍然返回全部。
        """
        await _seed_two_notes(test_db)
        await _trash(test_db, "n1")
        await _trash(test_db, "n2")

        assert await _visible_card_ids(test_db) == set(), (
            "全部笔记都在回收站里，却仍有卡片可见 —— 条件与外层的行无关"
        )

    @pytest.mark.asyncio
    async def test_dangling_note_id_is_visible(self, test_db):
        """悬挂引用（`note_id IS NULL`）语义上仍可见 —— 修 bug 不能改掉这条

        "提升的核心卡片"与"悬挂的题目"都靠它：它们不属于任何笔记，
        所以"笔记在不在回收站"对它们无从谈起。
        """
        from sqlalchemy import select

        from app.models.knowledge_card import KnowledgeCard

        await _seed_two_notes(test_db)
        await _trash(test_db, "n1")
        await _trash(test_db, "n2")

        async with test_db() as session:
            card = (await session.execute(
                select(KnowledgeCard).where(KnowledgeCard.id == "c1")
            )).scalar_one()
            card.note_id = None
            await session.commit()

        assert await _visible_card_ids(test_db) == {"c1"}

    @pytest.mark.asyncio
    async def test_generated_sql_does_not_put_the_outer_table_in_the_subquery(self, test_db):
        """★ 源码/SQL 级守卫：子查询的 FROM 里不能出现外层那张表

        这是这个 bug 的**直接签名**。把它钉住，将来有人"简化"掉
        `correlate_except` 时会当场变红，而不是等到某天有人发现
        "回收站里的东西还能搜到"。
        """
        from sqlalchemy import select

        from app.models.knowledge_card import KnowledgeCard
        from app.models.note import Note

        sql = str(select(KnowledgeCard.id).where(Note.not_trashed(KnowledgeCard.note_id)))
        assert "FROM notes, knowledge_cards" not in sql, (
            "子查询把外层表加进了自己的 FROM —— `not_trashed` 又变回恒真了"
        )
        # 子查询里只该有 notes 一张表（`FROM notes`），不该出现外层那张
        assert "FROM notes, " not in sql, f"子查询的 FROM 里多了一张表：\n{sql}"

    def test_source_uses_correlate_except_not_correlate_none(self):
        """★ 源码级守卫：不许回到 `correlate(None)`

        ⚠️ 判据只看**代码行**，不看注释与 docstring ——
        那个函数的 docstring 里**必须**引用 `.correlate(None)` 才能说清
        这个 bug 的来历（第一版整文件 grep，被自己的说明文字判红了，
        与 `test_ready_endpoint.py` 里那条注释误报是同一类坑）。
        """
        import ast
        import pathlib
        import re

        path = pathlib.Path(__file__).resolve().parents[1] / "app" / "models" / "note.py"
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)

        # 找出 not_trashed 的 AST 结点，只取它的**代码**（不含 docstring）
        target = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "not_trashed":
                target = node
                break
        assert target is not None, "note.py 里找不到 not_trashed"

        body_stmts = [
            stmt for stmt in target.body
            # 跳过 docstring（Expr(Constant(str))）
            if not (
                isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            )
        ]
        code = "\n".join(ast.unparse(s) for s in body_stmts)

        assert "correlate_except(cls)" in code, (
            "`not_trashed` 不再用 `correlate_except` —— 那会让条件变回恒真"
        )
        assert not re.search(r"\.correlate\(\s*None\s*\)", code), (
            "`correlate(None)` 回来了 —— 它会把外层的表加进子查询的 FROM"
        )
