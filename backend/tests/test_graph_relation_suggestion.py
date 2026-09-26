"""`auto_suggest_relations` 的选边规则（2026-09-26 改）

## 这个文件锁的是什么

判定逻辑此前**没有任何测试**（全仓只有 `test_structured_output.py:249` 引用了
`graph_service.py`，测的是 LLM 结构化输出那一块）。于是"边建不出来"这件事
一直没人发现：真实库 1181 张卡片只有 120 张有边，**1061 张是孤点**。

改动把选边方式从「相似度 ≥ 全局阈值 0.75 + 同笔记一律跳过」换成
「**每卡 Top-K + 绝对下界 + 只跳过同章节**」。依据与全部实测数字见
`docs/journal/relation-quality-2026-09-26.md`（现状 4 条新边 / 覆盖 8-200；
改后 443 条 / 197-200，抽样精确率 83%）。

## 两条最容易回潮、必须各占一条用例

1. **同笔记·跨章节必须被保留**（`:341-353`）。旧规则把它连同同章节噪音一起丢掉，
   而实测它占了被丢弃对的 **89.5%**，且相似度中位 0.529 **比跨笔记的 0.443 还高**。
   谁哪天把判据改回 `note_id`，这条会红。
2. **同笔记·同章节必须被跳过**。章节内才是真噪音源（余弦中位 0.667、p95 0.832）。

## 怎么做到不依赖模型与 Celery

`auto_suggest_relations` 通过 `celery_app.send_task` 拿向量。这里把它替换成
**按标题查表**的假任务 —— 于是"谁和谁像"由用例自己定，与嵌入模型无关，
也不需要在测试进程里加载 2.3GB 的 BGE-M3。

向量用二维单位向量：两张卡片的余弦相似度就是两个角度的余弦差，
所以"让 A 与 B 相似度 0.9"写成 `_vec(0)` 与 `_vec(acos(0.9))` 即可。
"""

import math

import pytest

#: 卡片 id 故意用「字典序与插入序相反」的一对，用来压住 `card_id_1 <= card_id_2`
#: 的存储约定（无向关系必须定序，否则同一对会以两种形态各存一行）。
ID_Z = "card-z"
ID_A = "card-a"


def _vec(deg: float) -> list:
    """角度制单位向量；两向量的余弦相似度 = cos(角度差)"""
    rad = math.radians(deg)
    return [math.cos(rad), math.sin(rad)]


def _sim_to(deg: float) -> float:
    """与 `_vec(0)` 的余弦相似度"""
    return math.cos(math.radians(deg))


async def _seed(test_db, notes, cards):
    """建用户 + 笔记 + 卡片

    notes: 笔记 id 列表
    cards: [(id, note_id, chapter_title_or_None, title)]
    """
    from app.models.knowledge_card import CardType, KnowledgeCard
    from app.models.note import Note, NoteStatus, SourceType
    from app.models.user import User

    async with test_db() as session:
        session.add(User(id="u1", email="a@x.c", username="a", hashed_password="x"))
        await session.flush()
        session.add_all(
            [
                Note(
                    id=nid,
                    user_id="u1",
                    title=nid,
                    source_type=SourceType.pdf,
                    status=NoteStatus.cleaned,
                    file_size=1,
                    original_file_path=f"/u1/{nid}.pdf",
                    original_md_path=f"/u1/{nid}.md",
                )
                for nid in notes
            ]
        )
        await session.flush()
        session.add_all(
            [
                KnowledgeCard(
                    id=cid,
                    user_id="u1",
                    note_id=nid,
                    card_type=CardType.concept,
                    title=title,
                    content="内容",
                    chapter_title=chap,
                )
                for cid, nid, chap, title in cards
            ]
        )
        await session.commit()


def _patch_vectors(monkeypatch, by_title):
    """把 Celery 编码任务换成"按标题查表"，并记录被请求的文本"""
    from app.tasks import celery_app as celery_module

    seen = {}

    def _send_task(_name, args=None, **_kwargs):
        texts = args[0]
        seen["texts"] = texts

        class _Task:
            def get(self, timeout=None):  # noqa: ARG002 — 签名要与 Celery 一致
                return [by_title[text.split("\n")[0]] for text in texts]

        return _Task()

    monkeypatch.setattr(celery_module.celery_app, "send_task", _send_task)
    return seen


async def _suggest(test_db, monkeypatch, by_title, user_id="u1"):
    from app.services import graph_service

    _patch_vectors(monkeypatch, by_title)
    async with test_db() as session:
        return await graph_service.auto_suggest_relations(user_id=user_id, db=session)


async def _pairs(test_db):
    """读出全部关系，返回 (id1, id2) 集合（在会话内取完值，避免脱离会话）"""
    from sqlalchemy import select

    from app.models.card_relation import CardRelation

    async with test_db() as session:
        rows = (await session.execute(select(CardRelation))).scalars().all()
        return {(r.card_id_1, r.card_id_2) for r in rows}


class TestNoteGranularity:
    """下界按**是否同笔记**分档（这是这条判据的第三次形态）

    ## 三次修正的经过（这个类里的用例就是那三次的守卫）

    - 旧规则：`note_id` 相同就跳过 → 误伤 89.5%（被丢弃的 7,470 对里 6,686 对是
      "同笔记跨章节"，而它们的相似度中位 0.529 **比跨笔记的 0.443 还高**）
    - 第二次：改成"只跳过同章节" → 仍错，同章节 ≥0.80 盲判 10/10 全真
    - 第三次（现在）：轴改成**笔记** —— 同笔记（含同章节）用 `MIN_SIMILARITY`，
      跨笔记要求 `CROSS_NOTE_MIN_SIMILARITY`。依据是盲判 v2 的笔记控制：
      四个层里同笔记对**全部**优于跨笔记对。
    """

    @pytest.mark.asyncio
    async def test_same_note_cross_chapter_pair_is_kept(self, test_db, monkeypatch):
        """★ 同笔记**跨章节**的卡片对必须建边（判据不能只用章节）"""
        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[
                ("c1", "n1", "第一章", "甲"),
                ("c2", "n1", "第二章", "乙"),
                ("c3", "n2", "第一章", "丙"),
            ],
        )
        await _suggest(
            test_db,
            monkeypatch,
            {"甲": _vec(0), "乙": _vec(26), "丙": _vec(120)},
        )
        assert ("c1", "c2") in await _pairs(test_db), (
            "同笔记跨章节的卡片对被丢掉了 —— 实测它占被丢弃对的 89.5%，"
            "且相似度中位比跨笔记还高"
        )

    @pytest.mark.asyncio
    async def test_same_chapter_pair_uses_the_normal_floor(self, test_db, monkeypatch):
        """★ 同章节对**不再特殊对待**：与其它同笔记对一样用 `MIN_SIMILARITY`

        盲判 v2 实测同章节 `[0.75,0.80)` 是 **92%** —— 前一版要求它 ≥0.80 是多余的。
        这一条在 `da775b2` 的规则下会红（那时 0.777 的同章节对会被跳过）。
        """
        from app.services.graph_service import MIN_SIMILARITY

        sim = _sim_to(39)  # cos 39° ≈ 0.777
        assert sim >= MIN_SIMILARITY, f"前提：要够到同笔记的下界，实际 {sim}"

        await _seed(
            test_db,
            notes=["n1"],
            cards=[
                ("c1", "n1", "第一章", "甲"),
                ("c2", "n1", "第一章", "乙"),
            ],
        )
        await _suggest(test_db, monkeypatch, {"甲": _vec(0), "乙": _vec(39)})
        assert ("c1", "c2") in await _pairs(test_db), (
            f"同章节对（相似度 {sim:.3f}）被跳过了 —— 实测这一段 92%，不该特殊对待"
        )

    @pytest.mark.asyncio
    async def test_cross_note_pair_needs_the_higher_floor(self, test_db, monkeypatch):
        """★ **跨笔记**对落在 `[MIN_SIMILARITY, CROSS_NOTE_MIN_SIMILARITY)` → 跳过

        这是本次修正的核心。盲判 v2 实测跨笔记 `0.75-0.85` 只有 **50%**
        （同笔记同段是 83%）—— 跨笔记的余弦更容易被"同领域"骗到。
        """
        from app.services.graph_service import (
            CROSS_NOTE_MIN_SIMILARITY,
            MIN_SIMILARITY,
        )

        sim = _sim_to(36)  # cos 36° ≈ 0.809
        assert MIN_SIMILARITY <= sim < CROSS_NOTE_MIN_SIMILARITY, f"前提不成立：{sim}"

        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[
                # 跨笔记，相似度 0.809 → 必须跳过
                ("c1", "n1", "第一章", "甲"),
                ("c2", "n2", "第一章", "乙"),
                # 同笔记，相似度同为 0.809 → **必须**建边，证明差异来自"是否同笔记"
                ("c3", "n1", "第二章", "丙"),
            ],
        )
        await _suggest(
            test_db,
            monkeypatch,
            {"甲": _vec(0), "乙": _vec(36), "丙": _vec(0)},
        )
        pairs = await _pairs(test_db)
        assert ("c1", "c2") not in pairs, (
            f"跨笔记对（相似度 {sim:.3f}）被建边了 —— 那一段实测只有 50%"
        )
        assert ("c1", "c3") in pairs, "同笔记的对照对没建边 —— 上面的断言可能只是'什么都没建'"

    @pytest.mark.asyncio
    async def test_cross_note_high_similarity_is_kept(self, test_db, monkeypatch):
        """反证：跨笔记对把相似度提到 ≥0.85 就必须建边"""
        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[("c1", "n1", "第一章", "甲"), ("c2", "n2", "第一章", "乙")],
        )
        await _suggest(test_db, monkeypatch, {"甲": _vec(0), "乙": _vec(26)})
        assert ("c1", "c2") in await _pairs(test_db)


class TestTopKSelection:
    """每卡 Top-K + 按是否同章节分档的下界"""

    @pytest.mark.asyncio
    async def test_below_floor_creates_nothing(self, test_db, monkeypatch):
        """低于绝对下界的对不建边"""
        from app.services.graph_service import MIN_SIMILARITY

        # 夹角 60° → 余弦 0.5
        assert _sim_to(60) < MIN_SIMILARITY
        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[("c1", "n1", "第一章", "甲"), ("c2", "n2", "第一章", "乙")],
        )
        await _suggest(test_db, monkeypatch, {"甲": _vec(0), "乙": _vec(60)})
        assert await _pairs(test_db) == set()

    @pytest.mark.asyncio
    async def test_mid_band_creates_nothing(self, test_db, monkeypatch):
        """★ 落在 `[0.55, 0.75)` 的跨章节对**不建边** —— 这是本次修正的核心

        上一批的下界是 0.55，这一对会被建出来。而盲判实测 `0.55-0.65` 那一段
        精确率只有 **10%**（Wilson 95% `[1.8%, 40.4%]`），与 `0.75-0.85` 的
        `[49.0%, 94.3%]` **不重叠** —— 也就是说那个下界把一整段噪音放了进来。

        夹角 50° → 余弦 0.643，正好落在被否掉的那一段里。
        """
        from app.services.graph_service import MIN_SIMILARITY

        sim = _sim_to(50)
        assert 0.55 <= sim < MIN_SIMILARITY, f"前提：要落在 [0.55, 0.75) 内，实际 {sim}"

        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[("c1", "n1", "第一章", "甲"), ("c2", "n2", "第一章", "乙")],
        )
        await _suggest(test_db, monkeypatch, {"甲": _vec(0), "乙": _vec(50)})
        assert await _pairs(test_db) == set(), (
            f"相似度 {sim:.3f} 的卡片对被建边了 —— 那一段盲判精确率只有 10%"
        )

    @pytest.mark.asyncio
    async def test_high_band_creates_edge(self, test_db, monkeypatch):
        """反证：同样两张卡，把相似度提到 0.85 以上就必须建边

        没有这一条，上面那条"不建边"的断言可能只是"什么都没建"。
        """
        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[("c1", "n1", "第一章", "甲"), ("c2", "n2", "第一章", "乙")],
        )
        await _suggest(test_db, monkeypatch, {"甲": _vec(0), "乙": _vec(26)})
        assert ("c1", "c2") in await _pairs(test_db)

    @pytest.mark.asyncio
    async def test_k_limits_edges_per_card(self, test_db, monkeypatch):
        """K 真的在限制：K=1 时边数不超过卡片数

        `K=1` 时每张卡最多主动选 1 个邻居，去重后边数上界就是 n。
        （**不能**断言"每个节点的度 ≤ K" —— 一张卡可以被很多别的卡选中，
        那是枢纽节点，是正常的。）
        """
        from app.services import graph_service

        cards = [(f"c{i}", f"n{i}", "第一章", f"卡{i}") for i in range(6)]
        by_title = {f"卡{i}": _vec(i * 10) for i in range(6)}
        await _seed(test_db, notes=[f"n{i}" for i in range(6)], cards=cards)

        monkeypatch.setattr(graph_service, "TOP_K_PER_CARD", 1)
        await _suggest(test_db, monkeypatch, by_title)
        k1 = len(await _pairs(test_db))
        assert k1 <= 6, f"K=1 时建了 {k1} 条边，超过卡片数"

        # 换一张干净的库再跑 K=3：边数必须变多，否则 K 根本没生效
        # （同一个 test_db 里已有关系会被跳过，所以要新起一个 fixture 调用）
        # 这里用"K=3 下界=0 且允许更多邻居"来对照：直接比 K=1 与 K=5
        monkeypatch.setattr(graph_service, "TOP_K_PER_CARD", 5)
        more = await _suggest(test_db, monkeypatch, by_title)
        assert more > 0, "放宽 K 之后一条新边都没有 —— K 没有生效"

    @pytest.mark.asyncio
    async def test_no_duplicate_pairs_and_storage_is_ordered(self, test_db, monkeypatch):
        """同一对只出现一次，且按 `card_id_1 <= card_id_2` 定序存储

        无向关系若不定序，同一对会以两种形态各存一行（`docs/decisions.md#F-17`
        记的就是这条）。
        """
        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[
                (ID_Z, "n1", "第一章", "甲"),
                (ID_A, "n2", "第一章", "乙"),
            ],
        )
        # 互相选中对方：这一对会被 i 与 j 各产生一次
        await _suggest(test_db, monkeypatch, {"甲": _vec(0), "乙": _vec(26)})
        pairs = await _pairs(test_db)
        assert pairs == {(ID_A, ID_Z)}, f"期望恰好一条定序后的边，实际 {pairs}"

    @pytest.mark.asyncio
    async def test_existing_relation_is_not_suggested_again(self, test_db, monkeypatch):
        """已有关系（含 rejected）不重复建议 —— 被拒过的不该再冒出来

        ⚠️ 断言的是「**没有新增 suggested 行**」，不是「这一对不存在」：
        那条 rejected 行本来就在库里，`_pairs` 会把它一起读出来。
        """
        from sqlalchemy import select

        from app.models.card_relation import CardRelation, RelationStatus, RelationType

        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[("c1", "n1", "第一章", "甲"), ("c2", "n2", "第一章", "乙")],
        )
        async with test_db() as session:
            session.add(
                CardRelation(
                    user_id="u1",
                    card_id_1="c1",
                    card_id_2="c2",
                    relation_type=RelationType.related,
                    status=RelationStatus.rejected,
                )
            )
            await session.commit()

        await _suggest(test_db, monkeypatch, {"甲": _vec(0), "乙": _vec(26)})

        async with test_db() as session:
            rows = (await session.execute(select(CardRelation))).scalars().all()
            assert len(rows) == 1, f"被拒过的卡片对又被建议了一次：{[(r.card_id_1, r.card_id_2, r.status) for r in rows]}"
            assert rows[0].status == RelationStatus.rejected, "原来那条 rejected 行被改动了"

    @pytest.mark.asyncio
    async def test_all_created_relations_are_suggested_and_typed_related(
        self, test_db, monkeypatch
    ):
        """新建的边一律是 `suggested` + `related`（嵌入路径不判类型，见报告 §6）"""
        from sqlalchemy import select

        from app.models.card_relation import CardRelation, RelationStatus, RelationType

        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[("c1", "n1", "第一章", "甲"), ("c2", "n2", "第一章", "乙")],
        )
        await _suggest(test_db, monkeypatch, {"甲": _vec(0), "乙": _vec(26)})
        async with test_db() as session:
            rows = (await session.execute(select(CardRelation))).scalars().all()
            assert rows, "一条边都没建，后面两条断言没有意义"
            assert all(r.status == RelationStatus.suggested for r in rows)
            assert all(r.relation_type == RelationType.related for r in rows)


class TestEarlyReturn:
    @pytest.mark.asyncio
    async def test_single_card_returns_zero_without_calling_embedding(
        self, test_db, monkeypatch
    ):
        """候选不足 2 张时直接返回 0，**不**去调嵌入服务"""
        await _seed(
            test_db,
            notes=["n1"],
            cards=[("c1", "n1", "第一章", "甲")],
        )
        seen = _patch_vectors(monkeypatch, {"甲": _vec(0)})
        from app.services import graph_service

        async with test_db() as session:
            assert await graph_service.auto_suggest_relations(user_id="u1", db=session) == 0
        assert "texts" not in seen, "候选不足时仍然调用了嵌入服务"
