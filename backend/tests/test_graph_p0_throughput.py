# -*- coding: utf-8 -*-
"""
A1 P0：图谱关系召回的吞吐与审核队列闸门
==========================================

## 三个改动各一组用例

| 项 | 改了什么 | 为什么 |
|---|---|---|
| **P0-1** | `MAX_CARDS_FOR_SUGGEST` 200 → 600 | 真库 **3028/3469 孤点（87.3%）**，而瓶颈是**吞吐不是质量**（2683/3028 的最近邻已过阈值，全量跑一遍只要 725 ms）。旧上限意味着"要跑 ~15 轮才轮到别人"，真库 `suggested` 至今为 **0** |
| **P0-2** | 候选按价值排序 + 产出按相似度截断到 `RELATION_BATCH_SIZE` | ⚠️ **P0-1 单独上线 = 审核队列爆炸**（7020 条待审等于没有）。排序保证"首屏是按价值排序的 200 条"，而不是"随便 200 条" |
| **P0-3** | 疑似重复（同名）**带出去**而不是只写日志 | 同名对相似度普遍 ~1.000、盲判全为真重复 —— 只不建边会让这个数据问题从"图谱里看得见"变成"只有日志里看得见" |

## 为什么这组用例必须存在

P0-1 是**一个常量的改动**，而它的后果（队列爆炸）不在这个常量里 ——
它在另一个函数的行为里。所以"改了常量"这件事本身没有可测的语义，
**可测的是闸门与排序**：那两条才有失效形态（闸门失效 ⇒ 一次几千条；
排序失效 ⇒ 首屏是随机 200 条）。
"""

from __future__ import annotations

import math

import pytest


def _vec(deg: float) -> list[float]:
    """单位向量（2 维就够算余弦，省得造 1024 维）"""
    r = math.radians(deg)
    return [math.cos(r), math.sin(r)]


async def _seed(test_db, *, notes, cards, vectors=None):
    """建用户 + 笔记 + 卡片，并把向量写进库

    cards: [(id, note_id, chapter, title)]
    vectors: {title: [float, ...]}；缺的按"没有向量"入库
    """
    from app.models.chunk import pack_vector
    from app.models.knowledge_card import CardType, KnowledgeCard
    from app.models.note import Note, NoteStatus, SourceType
    from app.models.user import User

    vectors = vectors or {}

    def _card(cid, nid, chap, title):
        vec = vectors.get(title)
        return KnowledgeCard(
            id=cid,
            user_id="u1",
            note_id=nid,
            card_type=CardType.concept,
            title=title,
            content="内容",
            chapter_title=chap,
            embedding=pack_vector(vec) if vec else None,
            embedding_dim=len(vec) if vec else None,
            embedding_model="test" if vec else None,
            has_embedding=bool(vec),
        )

    async with test_db() as session:
        session.add(User(id="u1", email="a@x.c", username="a", hashed_password="x"))
        await session.flush()
        session.add_all([
            Note(
                id=nid, user_id="u1", title=nid,
                source_type=SourceType.pdf, status=NoteStatus.cleaned,
                file_size=1, original_file_path=f"/u1/{nid}.pdf",
                original_md_path=f"/u1/{nid}.md",
            )
            for nid in notes
        ])
        await session.flush()
        session.add_all([_card(*c) for c in cards])
        await session.commit()


async def _suggest(test_db, user_id="u1"):
    from app.services.graph_service import auto_suggest_relations

    async with test_db() as session:
        return await auto_suggest_relations(user_id, session)


async def _suggested_scores(test_db):
    """`suggested` 关系的相似度降序列表"""
    from sqlalchemy import select

    from app.models.card_relation import CardRelation, RelationStatus

    async with test_db() as session:
        rows = list((await session.execute(
            select(CardRelation).where(CardRelation.status == RelationStatus.suggested)
        )).scalars().all())
    return sorted((r.similarity_score or 0.0 for r in rows), reverse=True)


class TestBatchGate:
    """P0-2 的"分批"那一半：无论算出多少，一轮最多写 `RELATION_BATCH_SIZE` 条"""

    @pytest.mark.asyncio
    async def test_writes_at_most_the_batch_size(self, test_db, monkeypatch):
        """★ 闸门生效：一轮产出被压到上限内

        造 6 张两两足够相似的卡（同一"簇"里彼此都过阈值），把闸门压到 2 ——
        不压闸门时这一批会产生远多于 2 条建议。
        """
        from app.services import graph_service

        await _seed(
            test_db,
            notes=["n1", "n2", "n3", "n4", "n5", "n6"],
            cards=[(f"c{i}", f"n{i}", "第一章", t) for i, t in enumerate(
                ["甲", "乙", "丙", "丁", "戊", "己"], start=1
            )],
            # 全部落在 0-50 度以内 ⇒ 两两余弦都过 0.80
            vectors={"甲": _vec(0), "乙": _vec(5), "丙": _vec(10),
                     "丁": _vec(15), "戊": _vec(20), "己": _vec(25)},
        )

        monkeypatch.setattr(graph_service, "RELATION_BATCH_SIZE", 2)
        created = await _suggest(test_db)

        assert created == 2, f"闸门没生效，写了 {created} 条"
        scores = await _suggested_scores(test_db)
        assert len(scores) == 2

    @pytest.mark.asyncio
    async def test_gate_keeps_the_highest_similarity_ones(self, test_db, monkeypatch):
        """★★ 闸门**不是随便留 N 条**：留下的必须是相似度最高的那些

        这是 P0-2 的核心判据，也是"审核队列首屏按价值排序"的实现依据。
        没有这条，把实现写成 `[:N]`（保持生成顺序）也能让上面那条绿 ——
        而那样首屏就是"随机 200 条"。
        """
        from app.services import graph_service

        # 甲最像乙（0 度 vs 3 度，几乎重合），丙丁戊彼此只是勉强过阈值
        await _seed(
            test_db,
            notes=[f"n{i}" for i in range(1, 8)],
            cards=[(f"c{i}", f"n{i}", "第一章", t) for i, t in enumerate(
                ["甲", "乙", "丙", "丁", "戊", "己", "庚"], start=1
            )],
            vectors={
                "甲": _vec(0), "乙": _vec(3),          # 余弦 ≈ 0.9986
                "丙": _vec(30), "丁": _vec(35), "戊": _vec(40),
                "己": _vec(45), "庚": _vec(50),
            },
        )

        monkeypatch.setattr(graph_service, "RELATION_BATCH_SIZE", 1)
        created = await _suggest(test_db)

        assert created == 1
        scores = await _suggested_scores(test_db)
        assert scores[0] > 0.99, (
            f"留下的那条相似度只有 {scores[0]:.4f} —— 闸门留下的不是最像的那些"
        )

    @pytest.mark.asyncio
    async def test_gate_of_zero_writes_nothing(self, test_db, monkeypatch):
        """边界：闸门为 0 时**一条都不写**（而不是"等于不限"）

        这类"0 表示不限"的隐式语义是经典陷阱 —— 若实现写成
        `if batch_size:` 就会在 0 时退化成不限，一次倾倒几千条。
        """
        from app.services import graph_service

        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[("c1", "n1", "第一章", "甲"), ("c2", "n2", "第一章", "乙")],
            vectors={"甲": _vec(0), "乙": _vec(3)},
        )

        monkeypatch.setattr(graph_service, "RELATION_BATCH_SIZE", 0)
        created = await _suggest(test_db)

        assert created == 0, f"闸门为 0 却写了 {created} 条"
        assert await _suggested_scores(test_db) == []

    @pytest.mark.asyncio
    async def test_gate_advances_across_runs(self, test_db, monkeypatch):
        """闸门不妨碍推进：第一轮留下的卡有边了，第二轮处理的是**下一批**孤点"""
        from app.services import graph_service

        await _seed(
            test_db,
            notes=[f"n{i}" for i in range(1, 7)],
            cards=[(f"c{i}", f"n{i}", "第一章", t) for i, t in enumerate(
                ["甲", "乙", "丙", "丁", "戊", "己"], start=1
            )],
            vectors={"甲": _vec(0), "乙": _vec(5), "丙": _vec(10),
                     "丁": _vec(15), "戊": _vec(20), "己": _vec(25)},
        )

        monkeypatch.setattr(graph_service, "RELATION_BATCH_SIZE", 2)
        first = await _suggest(test_db)
        second = await _suggest(test_db)

        assert first == 2 and second == 2, f"两轮各应写 2 条，实际 {first} / {second}"
        all_ids = set()
        from sqlalchemy import select

        from app.models.card_relation import CardRelation

        async with test_db() as session:
            for r in (await session.execute(select(CardRelation))).scalars().all():
                all_ids.add(r.card_id_1)
                all_ids.add(r.card_id_2)
        assert len(all_ids) >= 3, (
            f"两轮只碰到 {len(all_ids)} 张卡 —— 第二轮没有推进到新的孤点"
        )


class TestCandidateSortKey:
    """P0-2 的"排序"那一半：`_candidate_sort_key` 的判据必须是确定的"""

    def _key(self, cid, *, edges, notes, ts):
        from app.services.graph_service import _candidate_sort_key

        return _candidate_sort_key(
            cid,
            edge_count=edges,
            note_size=notes,
            updated_at=ts,
        )

    def test_fewer_edges_sort_first(self):
        """★ 第 1 判据：边少的先（它们连"看起来相关"都做不到）"""
        k_few = self._key("a", edges={"a": 0, "b": 3}, notes={}, ts={})
        k_many = self._key("b", edges={"a": 0, "b": 3}, notes={}, ts={})
        assert k_few < k_many

    def test_smaller_note_sorts_first_when_edges_tie(self):
        """第 2 判据：所属笔记卡片少的先（小笔记里的卡更容易被淹没）"""
        k_small = self._key("a", edges={}, notes={"a": 2, "b": 50}, ts={})
        k_big = self._key("b", edges={}, notes={"a": 2, "b": 50}, ts={})
        assert k_small < k_big

    def test_missing_updated_at_sorts_last_not_crashes(self):
        """★ 缺 `updated_at` 的老数据**排到最后**，而不是让比较抛 TypeError

        `None` 与 `datetime` 不能比大小 —— 直接放进键里会在真实数据上炸。
        """
        from datetime import datetime, timezone

        k_has = self._key("a", edges={}, notes={}, ts={"a": datetime.now(timezone.utc)})
        k_none = self._key("b", edges={}, notes={}, ts={"b": None})
        assert k_has < k_none, "有时间的应排在没时间的前面"

    def test_tie_is_broken_by_id_so_order_is_reproducible(self):
        """★ 全部并列时按 id 定序 —— 同一批候选每次跑出**同样的顺序**

        没有这一位，同样的输入可能因为集合/字典的迭代顺序不同而给出不同结果，
        而"可复现"是能对着两次运行做对比的前提。
        """
        keys = [self._key(c, edges={}, notes={}, ts={}) for c in ("c", "a", "b")]
        assert [k[0] for k in sorted(keys)] == [0, 0, 0]  # 前两位确实并列
        assert sorted(keys)[0][-1] == "a", "并列时没有按 id 定序"
        assert sorted(keys)[-1][-1] == "c"

    def test_ordering_is_total_and_stable(self):
        """排序键必须构成**全序**（任意两张卡都能比出先后，且不自相矛盾）"""
        keys = {
            c: self._key(c, edges={c: i}, notes={c: i % 3}, ts={})
            for i, c in enumerate("abcdef")
        }
        ordered = sorted(keys, key=lambda c: keys[c])
        assert len(ordered) == len(set(ordered)), "排序把两张卡判成了同一位"


class TestSuspectedDuplicatesVisible:
    """P0-3：同名（疑似重复）对**不建边**，但必须能被看见

    真库实测：同名对的相似度普遍 ~1.000，盲判过的 3 条**全部是真重复**。
    只写日志的话，这个数据问题就从"图谱里看得见"变成"只有日志里看得见"。
    """

    @pytest.mark.asyncio
    async def test_same_title_pair_creates_no_edge(self, test_db):
        """同名对不建边（既有行为，这里锁住它没被 P0 改动带坏）"""
        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[("c1", "n1", "第一章", "操作术语"), ("c2", "n2", "第一章", "操作术语")],
            vectors={"操作术语": _vec(0)},  # 两张卡共用同一个向量
        )
        created = await _suggest(test_db)
        assert created == 0, "同名卡片被连了边 —— 那是重复卡片，不是相关卡片"

    @pytest.mark.asyncio
    async def test_neighbouring_titles_are_still_linked(self, test_db):
        """反向：**相近但不同名**的标题照常连边（同名闸不能误挡）"""
        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[("c1", "n1", "第一章", "操作术语"), ("c2", "n2", "第一章", "操作术语定义")],
            vectors={"操作术语": _vec(0), "操作术语定义": _vec(3)},
        )
        created = await _suggest(test_db)
        assert created == 1, "相近标题被误挡了 —— 它们不是重复卡片（总表 vs 定义）"
