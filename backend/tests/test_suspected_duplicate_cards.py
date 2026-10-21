# -*- coding: utf-8 -*-
"""
疑似重复卡片的可见化（A1 P0-3）
=================================

## 这一组盯的是什么

`auto_suggest_relations` 里有一道「同名闸」：标题相同的两张卡**不建边**。
理由是实测的 —— 同名对的相似度**普遍 ~1.000**、盲判过的 3 条**全部是真重复**。
它们不是"错的边"，是**重复的卡片**。

但闸门只做了"不建边"，于是这个数据问题从"图谱里看得见"变成了
**"只有日志里看得见"**。本轮把它变成用户能看见的。

## 两条不能破的边界

1. **只报不处理**：合并/删除要另开一轮单独批（它会动 `review_states`）。
   本组有一条只读性守卫。
2. **不能与同名闸的判据打架**：闸门挡的那些对，正是这里要报的那些对 ——
   判据必须**完全一致**（都只 `strip()`、都不做更激进的归一），
   否则会出现"闸门挡了、清单里却没有"这种用户永远查不明白的状态。
"""

from __future__ import annotations

import math

import pytest


def _vec(deg: float) -> list[float]:
    """单位向量（2 维够算余弦）"""
    r = math.radians(deg)
    return [math.cos(r), math.sin(r)]


async def _seed(test_db, *, notes, cards, vectors=None, models=None):
    """cards: [(id, note_id, title, content)]；vectors: {card_id: [float,...]}"""
    from app.models.chunk import pack_vector
    from app.models.knowledge_card import CardType, KnowledgeCard
    from app.models.note import Note, NoteStatus, SourceType
    from app.models.user import User

    vectors = vectors or {}
    models = models or {}

    def _card(cid, nid, title, content):
        vec = vectors.get(cid)
        return KnowledgeCard(
            id=cid, user_id="u1", note_id=nid, card_type=CardType.concept,
            title=title, content=content,
            embedding=pack_vector(vec) if vec else None,
            embedding_dim=len(vec) if vec else None,
            embedding_model=models.get(cid, "test-model") if vec else None,
            has_embedding=bool(vec),
        )

    async with test_db() as session:
        session.add(User(id="u1", email="a@x.c", username="a", hashed_password="x"))
        await session.flush()
        session.add_all([
            Note(id=nid, user_id="u1", title=nid, source_type=SourceType.pdf,
                 status=NoteStatus.cleaned, file_size=1,
                 original_file_path=f"/u1/{nid}.pdf", original_md_path=f"/u1/{nid}.md")
            for nid in notes
        ])
        await session.flush()
        session.add_all([_card(*c) for c in cards])
        await session.commit()


async def _find(test_db, **kwargs):
    from app.services.graph_service import find_suspected_duplicate_cards

    async with test_db() as session:
        return await find_suspected_duplicate_cards("u1", session, **kwargs)


class TestDetection:
    @pytest.mark.asyncio
    async def test_same_title_near_identical_cards_are_reported(self, test_db):
        """★ 同名 + 相似度 ~1.0 ⇒ 报出来（真库里那 187 张卡的形态）"""
        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[
                ("c1", "n1", "操作术语", "按下表示合上。"),
                ("c2", "n2", "操作术语", "按下表示合上。"),
            ],
            vectors={"c1": _vec(0), "c2": _vec(1)},
        )
        pairs = await _find(test_db)
        assert len(pairs) == 1, f"同名近重复没被报出来：{pairs}"
        p = pairs[0]
        assert p["title"] == "操作术语"
        assert p["similarity"] > 0.99
        assert {p["card_id_1"], p["card_id_2"]} == {"c1", "c2"}

    @pytest.mark.asyncio
    async def test_same_title_but_different_content_is_not_reported(self, test_db):
        """★★ 反向断言：同名但**确实是两件事**的不报

        真库反例：`操作术语`（总表）与 `操作术语定义`（定义）是**不同**的卡。
        更强的一版是这里：标题**完全相同**但内容差很远 ——
        若判据只看标题，这张也会被列进"疑似重复"，而它是两张正当的卡。
        """
        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[
                ("c1", "n1", "接地方式", "中性点直接接地，接地电流大。"),
                ("c2", "n2", "接地方式", "经消弧线圈接地，用于补偿电容电流。"),
            ],
            vectors={"c1": _vec(0), "c2": _vec(80)},
        )
        assert await _find(test_db) == [], "同名但内容不同被误报成重复"

    @pytest.mark.asyncio
    async def test_different_titles_are_never_reported(self, test_db):
        """标题不同 ⇒ 不报（这就是「同名闸」的判据边界）"""
        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[
                ("c1", "n1", "操作术语", "按下表示合上。"),
                ("c2", "n2", "操作术语定义", "按下表示合上。"),
            ],
            vectors={"c1": _vec(0), "c2": _vec(0)},
        )
        assert await _find(test_db) == []

    @pytest.mark.asyncio
    async def test_title_whitespace_is_normalized_like_the_gate(self, test_db):
        """★ 判据必须与同名闸**完全一致**：都只 `strip()`

        若两边判据不同，会出现"闸门挡了、清单里却没有"——
        用户永远查不明白为什么这两张卡既不连边也不在疑似重复清单里。
        """
        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[
                ("c1", "n1", "操作术语", "按下表示合上。"),
                ("c2", "n2", "  操作术语  ", "按下表示合上。"),
            ],
            vectors={"c1": _vec(0), "c2": _vec(1)},
        )
        assert len(await _find(test_db)) == 1

    @pytest.mark.asyncio
    async def test_empty_titles_are_not_grouped(self, test_db):
        """标题为空**不**算同名 —— 空标题卡之间没有"同一个知识点"的关系

        这条要是不守，真库里几张空标题卡会被两两配成"疑似重复"，
        而它们之间唯一的共同点就是"都没标题"。
        """
        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[("c1", "n1", "   ", "内容甲"), ("c2", "n2", "", "内容乙")],
            vectors={"c1": _vec(0), "c2": _vec(0)},
        )
        assert await _find(test_db) == []

    @pytest.mark.asyncio
    async def test_mixed_embedding_models_are_not_compared(self, test_db):
        """★ 跨模型余弦没有意义 —— 不同模型的同名对不报

        与 `test_graph_relation_suggestion.py::test_mixed_embedding_models_are_not_compared`
        同一条判据。跨模型算出来的 0.99 是**巧合**，不是重复的证据。
        """
        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[
                ("c1", "n1", "操作术语", "按下表示合上。"),
                ("c2", "n2", "操作术语", "按下表示合上。"),
            ],
            vectors={"c1": _vec(0), "c2": _vec(0)},
            models={"c1": "BAAI/bge-m3", "c2": "BAAI/bge-small-zh-v1.5"},
        )
        assert await _find(test_db) == [], "跨模型被拿来算余弦了"

    @pytest.mark.asyncio
    async def test_cards_without_vectors_are_skipped(self, test_db):
        """没向量的卡算不出相似度 ⇒ 跳过（不按标题下"疑似重复"的结论）"""
        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[
                ("c1", "n1", "操作术语", "按下表示合上。"),
                ("c2", "n2", "操作术语", "按下表示合上。"),  # 不给向量
            ],
            vectors={"c1": _vec(0)},
        )
        assert await _find(test_db) == []

    @pytest.mark.asyncio
    async def test_zero_vector_does_not_become_zero_similarity(self, test_db):
        """★ 模长为 0 的坏向量要**跳过**，不能算成"完全不相似"

        算成 0 的话它会静默消失在清单里（看着像"没有重复"），
        而真相是"这条向量坏了"。两件事必须能分开 —— 所以这里选择跳过
        而不是报 0（报 0 会连"被看见"的机会都没有）。
        """
        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[
                ("c1", "n1", "操作术语", "按下表示合上。"),
                ("c2", "n2", "操作术语", "按下表示合上。"),
            ],
            vectors={"c1": [0.0, 0.0], "c2": _vec(0)},
        )
        # 两个都不报（c1 坏、c2 没有可比的同伴）
        assert await _find(test_db) == []

    @pytest.mark.asyncio
    async def test_trashed_note_cards_are_excluded(self, test_db):
        """回收站里的卡不参与（与图谱其余部分的判据一致）"""
        from datetime import datetime, timezone

        from sqlalchemy import select

        from app.models.note import Note

        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[
                ("c1", "n1", "操作术语", "按下表示合上。"),
                ("c2", "n2", "操作术语", "按下表示合上。"),
            ],
            vectors={"c1": _vec(0), "c2": _vec(1)},
        )
        async with test_db() as session:
            n2 = (await session.execute(select(Note).where(Note.id == "n2"))).scalar_one()
            n2.trashed_at = datetime.now(timezone.utc)
            await session.commit()

        assert await _find(test_db) == []


class TestDeterminism:
    @pytest.mark.asyncio
    async def test_order_is_by_similarity_then_ids(self, test_db):
        """★ 顺序确定：相似度降序 → `(card_id_1, card_id_2)`

        清单要能对着两次运行做 diff。没有末位定序时，
        同分的两对谁先谁后取决于字典/集合顺序 —— 报告就"变了"。
        """
        await _seed(
            test_db,
            notes=["n1", "n2", "n3"],
            cards=[
                ("c1", "n1", "甲", "内容"),
                ("c2", "n2", "甲", "内容"),
                ("c3", "n3", "乙", "内容"),
                ("c4", "n3", "乙", "内容"),
            ],
            # 甲乙两组内部相似度不同：甲更近
            vectors={"c1": _vec(0), "c2": _vec(1), "c3": _vec(0), "c4": _vec(10)},
        )
        pairs = await _find(test_db)
        assert len(pairs) == 2, pairs
        assert pairs[0]["title"] == "甲", "相似度高的那组没排在前面"
        assert pairs[0]["similarity"] > pairs[1]["similarity"]

    @pytest.mark.asyncio
    async def test_limit_truncates_but_keeps_the_worst_first(self, test_db):
        """`limit` 截断时留下的是**相似度最高**的那些"""
        cards = [(f"c{i}", "n1", "同名", "内容") for i in range(6)]
        await _seed(
            test_db,
            notes=["n1"],
            cards=cards,
            vectors={f"c{i}": _vec(i * 3) for i in range(6)},
        )
        pairs = await _find(test_db, limit=2)
        assert len(pairs) == 2
        assert pairs[0]["similarity"] >= pairs[1]["similarity"]

    @pytest.mark.asyncio
    async def test_no_same_title_cards_returns_empty(self, test_db):
        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[("c1", "n1", "甲", "内容"), ("c2", "n2", "乙", "内容")],
            vectors={"c1": _vec(0), "c2": _vec(0)},
        )
        assert await _find(test_db) == []


class TestReadOnly:
    """★ A1 P0-3 **只报不处理**（已拍板：合并/删除另开一轮单独批）"""

    def test_service_function_has_no_write_calls(self):
        import pathlib

        src = pathlib.Path(
            pathlib.Path(__file__).resolve().parents[1]
            / "app" / "services" / "graph_service.py"
        ).read_text(encoding="utf-8")
        # 只截出这个函数体（整个模块别处确实有写操作）
        start = src.index("async def find_suspected_duplicate_cards")
        end = src.index("def _unpack_vector", start)
        body = src[start:end]
        for forbidden in ("db.add(", "db.delete(", "session.add(", "update("):
            assert forbidden not in body, (
                f"find_suspected_duplicate_cards 里出现了写操作：{forbidden}"
            )
        assert "commit" not in body, "疑似重复的查询提交了事务？"

    def test_duplicate_rules_share_the_same_constant(self):
        """清单与闸门必须用**同一个**阈值常量（各写一份会漂）"""
        import pathlib

        src = pathlib.Path(
            pathlib.Path(__file__).resolve().parents[1]
            / "app" / "services" / "graph_service.py"
        ).read_text(encoding="utf-8")
        start = src.index("async def find_suspected_duplicate_cards")
        end = src.index("def _unpack_vector", start)
        assert "SUSPECTED_DUPLICATE_SIMILARITY" in src[start:end], (
            "阈值被硬编码了 —— 与闸门那边的常量脱钩"
        )
