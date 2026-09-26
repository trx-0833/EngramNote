"""`auto_suggest_relations` 的选边规则与向量取数（2026-09-26 改）

## 这个文件锁的是什么

判定逻辑此前**没有任何测试**（全仓只有 `test_structured_output.py:249` 引用了
`graph_service.py`，测的是 LLM 结构化输出那一块）。于是"边建不出来"这件事
一直没人发现：真实库 1181 张卡片只有 120 张有边，**1061 张是孤点**。

依据与全部实测数字见 `docs/journal/relation-quality-2026-09-26.md`。

## 三组最容易回潮、必须各占一条用例

1. **同笔记·跨章节必须被保留**。旧规则把它连同同章节噪音一起丢掉，
   而实测它占了被丢弃对的 **89.5%**，且相似度中位 0.529 **比跨笔记的 0.443 还高**。
2. **跨笔记要用更高的下界**（`CROSS_NOTE_MIN_SIMILARITY`）。盲判 v2 实测
   跨笔记 `0.75-0.85` 只有 50%，而同笔记同段是 83%。
3. **没有向量的卡片不参与**，且"有卡片但一条向量都没有"要**报错**而不是静默返回 0
   —— 后者会让用户以为"没有相关卡片"，而实际是没跑补向量。

## 怎么做到不依赖模型

向量不再由 Celery 现算（那正是候选池被压在 200 张的原因），而是**从库里读**。
所以用例直接把设计好的向量写进 `knowledge_cards.embedding` —— 于是"谁和谁像"
由用例自己定，与嵌入模型无关，也不需要在测试进程里加载 2.3GB 的 BGE-M3。

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


async def _seed(test_db, notes, cards, vectors=None):
    """建用户 + 笔记 + 卡片，并把向量**写进库**

    notes: 笔记 id 列表
    cards: [(id, note_id, chapter_title_or_None, title)]
    vectors: {title: [float, ...]}；缺的标题按"没有向量"入库（`has_embedding=False`）
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
        session.add_all([_card(*c) for c in cards])
        await session.commit()


async def _suggest(test_db, user_id="u1"):
    """跑一次真实函数（向量从库里读，不需要任何 mock）"""
    from app.services import graph_service

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
    async def test_same_note_cross_chapter_pair_is_kept(self, test_db):
        """★ 同笔记**跨章节**的卡片对必须建边（判据不能只用章节）"""
        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[
                ("c1", "n1", "第一章", "甲"),
                ("c2", "n1", "第二章", "乙"),
                ("c3", "n2", "第一章", "丙"),
            ],
            vectors={"甲": _vec(0), "乙": _vec(26), "丙": _vec(120)},
        )
        await _suggest(test_db)
        assert ("c1", "c2") in await _pairs(test_db), (
            "同笔记跨章节的卡片对被丢掉了 —— 实测它占被丢弃对的 89.5%，"
            "且相似度中位比跨笔记还高"
        )

    @pytest.mark.asyncio
    async def test_same_chapter_pair_uses_the_normal_floor(self, test_db):
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
            cards=[("c1", "n1", "第一章", "甲"), ("c2", "n1", "第一章", "乙")],
            vectors={"甲": _vec(0), "乙": _vec(39)},
        )
        await _suggest(test_db)
        assert ("c1", "c2") in await _pairs(test_db), (
            f"同章节对（相似度 {sim:.3f}）被跳过了 —— 实测这一段 92%，不该特殊对待"
        )

    @pytest.mark.asyncio
    async def test_cross_note_pair_needs_the_higher_floor(self, test_db):
        """★ **跨笔记**对落在 `[MIN_SIMILARITY, CROSS_NOTE_MIN_SIMILARITY)` → 跳过

        这是第三次修正的核心。盲判 v2 实测跨笔记 `0.75-0.85` 只有 **50%**
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
            vectors={"甲": _vec(0), "乙": _vec(36), "丙": _vec(0)},
        )
        await _suggest(test_db)
        pairs = await _pairs(test_db)
        assert ("c1", "c2") not in pairs, (
            f"跨笔记对（相似度 {sim:.3f}）被建边了 —— 那一段实测只有 50%"
        )
        assert ("c1", "c3") in pairs, "同笔记的对照对没建边 —— 上面的断言可能只是'什么都没建'"

    @pytest.mark.asyncio
    async def test_cross_note_high_similarity_is_kept(self, test_db):
        """反证：跨笔记对把相似度提到 ≥0.85 就必须建边"""
        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[("c1", "n1", "第一章", "甲"), ("c2", "n2", "第一章", "乙")],
            vectors={"甲": _vec(0), "乙": _vec(26)},
        )
        await _suggest(test_db)
        assert ("c1", "c2") in await _pairs(test_db)


class TestTopKSelection:
    """每卡 Top-K + 按是否同笔记分档的下界"""

    @pytest.mark.asyncio
    async def test_below_floor_creates_nothing(self, test_db):
        """低于绝对下界的对不建边"""
        from app.services.graph_service import MIN_SIMILARITY

        # 夹角 60° → 余弦 0.5
        assert _sim_to(60) < MIN_SIMILARITY
        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[("c1", "n1", "第一章", "甲"), ("c2", "n2", "第一章", "乙")],
            vectors={"甲": _vec(0), "乙": _vec(60)},
        )
        await _suggest(test_db)
        assert await _pairs(test_db) == set()

    @pytest.mark.asyncio
    async def test_mid_band_creates_nothing(self, test_db):
        """★ 落在 `[0.55, 0.75)` 的对**不建边**

        `f454a1b` 的下界是 0.55，这一对会被建出来。而盲判实测 `0.55-0.65` 那一段
        精确率只有 **10%**（Wilson 95% `[1.8%, 40.4%]`），与 `0.75-0.85` 的
        `[49.0%, 94.3%]` **不重叠** —— 那个下界把一整段噪音放了进来。

        夹角 50° → 余弦 0.643，正好落在被否掉的那一段里。
        """
        from app.services.graph_service import MIN_SIMILARITY

        sim = _sim_to(50)
        assert 0.55 <= sim < MIN_SIMILARITY, f"前提：要落在 [0.55, 0.75) 内，实际 {sim}"

        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[("c1", "n1", "第一章", "甲"), ("c2", "n2", "第一章", "乙")],
            vectors={"甲": _vec(0), "乙": _vec(50)},
        )
        await _suggest(test_db)
        assert await _pairs(test_db) == set(), (
            f"相似度 {sim:.3f} 的卡片对被建边了 —— 那一段盲判精确率只有 10%"
        )

    @pytest.mark.asyncio
    async def test_high_band_creates_edge(self, test_db):
        """反证：同样两张卡，把相似度提到 0.85 以上就必须建边

        没有这一条，上面那条"不建边"的断言可能只是"什么都没建"。
        """
        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[("c1", "n1", "第一章", "甲"), ("c2", "n2", "第一章", "乙")],
            vectors={"甲": _vec(0), "乙": _vec(26)},
        )
        await _suggest(test_db)
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
        vectors = {f"卡{i}": _vec(i * 10) for i in range(6)}
        await _seed(test_db, notes=[f"n{i}" for i in range(6)], cards=cards, vectors=vectors)

        monkeypatch.setattr(graph_service, "TOP_K_PER_CARD", 1)
        await _suggest(test_db)
        k1 = len(await _pairs(test_db))
        assert k1 <= 6, f"K=1 时建了 {k1} 条边，超过卡片数"

    @pytest.mark.asyncio
    async def test_larger_k_creates_more_edges(self, test_db, monkeypatch):
        """K 更大 → 边更多（对照组，证明上面的上界不是因为"什么都没建"）

        ⚠️ 用**独立的库**跑：候选池现在是"孤点优先"，同一个库里跑完 K=1
        之后所有卡都有边了，再跑 K=5 会因为"没有孤点"而返回 0 ——
        那不是 K 没生效，是候选池已经空了（见 `TestIsolationFirstPool`）。
        """
        from app.services import graph_service

        cards = [(f"c{i}", f"n{i}", "第一章", f"卡{i}") for i in range(6)]
        vectors = {f"卡{i}": _vec(i * 4) for i in range(6)}  # 两两都够 0.85
        await _seed(test_db, notes=[f"n{i}" for i in range(6)], cards=cards, vectors=vectors)

        monkeypatch.setattr(graph_service, "TOP_K_PER_CARD", 5)
        await _suggest(test_db)
        assert len(await _pairs(test_db)) > 0, "K=5 时一条边都没建"

    @pytest.mark.asyncio
    async def test_no_duplicate_pairs_and_storage_is_ordered(self, test_db):
        """同一对只出现一次，且按 `card_id_1 <= card_id_2` 定序存储

        无向关系若不定序，同一对会以两种形态各存一行（`docs/decisions.md#F-17`
        记的就是这条）。
        """
        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[(ID_Z, "n1", "第一章", "甲"), (ID_A, "n2", "第一章", "乙")],
            vectors={"甲": _vec(0), "乙": _vec(26)},
        )
        # 互相选中对方：这一对会被 i 与 j 各产生一次
        await _suggest(test_db)
        pairs = await _pairs(test_db)
        assert pairs == {(ID_A, ID_Z)}, f"期望恰好一条定序后的边，实际 {pairs}"

    @pytest.mark.asyncio
    async def test_existing_relation_is_not_suggested_again(self, test_db):
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
            vectors={"甲": _vec(0), "乙": _vec(26)},
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

        await _suggest(test_db)

        async with test_db() as session:
            rows = (await session.execute(select(CardRelation))).scalars().all()
            assert len(rows) == 1, (
                f"被拒过的卡片对又被建议了一次："
                f"{[(r.card_id_1, r.card_id_2, r.status) for r in rows]}"
            )
            assert rows[0].status == RelationStatus.rejected, "原来那条 rejected 行被改动了"

    @pytest.mark.asyncio
    async def test_all_created_relations_are_suggested_and_typed_related(self, test_db):
        """新建的边一律是 `suggested` + `related`（嵌入路径不判类型，见报告 §6）"""
        from sqlalchemy import select

        from app.models.card_relation import CardRelation, RelationStatus, RelationType

        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[("c1", "n1", "第一章", "甲"), ("c2", "n2", "第一章", "乙")],
            vectors={"甲": _vec(0), "乙": _vec(26)},
        )
        await _suggest(test_db)
        async with test_db() as session:
            rows = (await session.execute(select(CardRelation))).scalars().all()
            assert rows, "一条边都没建，后面两条断言没有意义"
            assert all(r.status == RelationStatus.suggested for r in rows)
            assert all(r.relation_type == RelationType.related for r in rows)


class TestVectorSourcing:
    """向量**从库里读**（2026-09-26 新增）—— 这组用例锁的是"缺向量"的行为

    ## 为什么值得单独一组

    换掉"每次现算"之后，多了一条新的失败模式：**卡片有、但向量没生成**。
    如果这时静默返回 0 条建议，用户看到的是"没有相关卡片"——
    与"没跑补向量"是完全不同的两件事，却长得一模一样。所以这里要锁住：
    缺向量 → 那些卡不参与；**一条向量都没有 → 报错并说清怎么补**。
    """

    @pytest.mark.asyncio
    async def test_cards_without_embedding_do_not_participate(self, test_db):
        """没有向量的卡片不参与建议，但**不影响**其它卡片照常建边"""
        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[
                ("c1", "n1", "第一章", "甲"),
                ("c2", "n2", "第一章", "乙"),
                ("c3", "n1", "第二章", "丙"),  # 故意不给向量
            ],
            vectors={"甲": _vec(0), "乙": _vec(26)},
        )
        await _suggest(test_db)
        pairs = await _pairs(test_db)
        assert ("c1", "c2") in pairs, "有向量的两张卡没建边 —— 缺向量的卡把整批带崩了"
        assert all("c3" not in p for p in pairs), "没有向量的卡片参与了建议"

    @pytest.mark.asyncio
    async def test_no_embeddings_at_all_raises_actionable_error(self, test_db):
        """★ 卡片有、但**一条向量都没有** → 报错，而不是静默返回 0

        静默返回 0 会让用户以为"没有相关卡片"。报错信息里必须给出补向量的办法。
        """
        from app.services.graph_service import GraphSuggestionError

        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[("c1", "n1", "第一章", "甲"), ("c2", "n2", "第一章", "乙")],
        )
        with pytest.raises(GraphSuggestionError) as exc:
            await _suggest(test_db)
        assert "embed_cards" in str(exc.value), (
            f"报错信息没告诉用户怎么补向量：{exc.value}"
        )

    @pytest.mark.asyncio
    async def test_corrupt_embedding_blob_is_treated_as_missing(self, test_db):
        """BLOB 长度与 `embedding_dim` 不符 → 按"没有向量"处理，**不**拿去算余弦

        长度不对的向量算出来的余弦是无意义的数字，而它会**静默参与排序** ——
        比"没有向量"更糟（`models/chunk.py:unpack_vector` 的注释说的就是这条）。
        """
        from app.models.chunk import pack_vector
        from app.models.knowledge_card import KnowledgeCard

        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[
                ("c1", "n1", "第一章", "甲"),
                ("c2", "n2", "第一章", "乙"),
                ("c3", "n1", "第二章", "丙"),
            ],
            vectors={"甲": _vec(0), "乙": _vec(26), "丙": _vec(26)},
        )
        # 把 c3 的 BLOB 截断一半，但 dim 仍写 2 → 长度对不上
        async with test_db() as session:
            from sqlalchemy import select

            card = (await session.execute(
                select(KnowledgeCard).where(KnowledgeCard.id == "c3")
            )).scalar_one()
            card.embedding = pack_vector([1.0, 0.0])[:4]
            await session.commit()

        await _suggest(test_db)
        pairs = await _pairs(test_db)
        assert all("c3" not in p for p in pairs), (
            "BLOB 长度不对的卡片仍然参与了建议 —— 那种向量算出来的相似度是无意义的"
        )
        assert ("c1", "c2") in pairs, "损坏一张卡把整批带崩了"

    @pytest.mark.asyncio
    async def test_mixed_embedding_models_are_not_compared(self, test_db):
        """★ 混用**两个模型**的向量不能放在一起算余弦（端到端验证时真的触发过）

        `EmbeddingService` 在可用内存不足时会自动降级
        （bge-m3 1024 维 → bge-small-zh-v1.5 512 维，见 `embedding_service.py:66`），
        所以同一个库里可能混着两个模型的向量。跨模型算出来的余弦是无意义的数字。

        这里让"少数派"那张卡的向量与 c1 **完全相同**（余弦 1.0）—— 只要它被算进去，
        就一定会建边。断言它一条边都没有。
        """
        from app.models.chunk import pack_vector
        from app.models.knowledge_card import KnowledgeCard

        await _seed(
            test_db,
            notes=["n1", "n2", "n3", "n4"],
            cards=[
                ("c1", "n1", "第一章", "甲"),
                ("c2", "n2", "第一章", "乙"),
                ("c3", "n3", "第一章", "丙"),
                ("c4", "n4", "第一章", "丁"),
            ],
            vectors={"甲": _vec(0), "乙": _vec(26), "丙": _vec(40), "丁": _vec(0)},
        )
        # 把 c4 标成另一个模型（向量与 c1 一模一样 → 若参与必然建边）
        async with test_db() as session:
            from sqlalchemy import select

            card = (await session.execute(
                select(KnowledgeCard).where(KnowledgeCard.id == "c4")
            )).scalar_one()
            card.embedding_model = "另一个模型"
            card.embedding = pack_vector(_vec(0))
            await session.commit()

        await _suggest(test_db)
        pairs = await _pairs(test_db)
        assert all("c4" not in p for p in pairs), (
            f"另一个模型的向量被拿来算余弦了：{pairs} —— 跨模型相似度无意义"
        )
        assert ("c1", "c2") in pairs, "多数派那一组没建边 —— 上面的断言可能只是'什么都没建'"


def test_model_name_reports_the_actually_loaded_model(monkeypatch):
    """★ `embedding_model` 列必须记**实际加载的**模型，不是配置值

    第一次实现取的是 `getattr(service, "model_name", None) or settings.embedding_model`，
    而 `EmbeddingService` 没有 `model_name` 属性（只有属性 `loaded_model_name`），
    于是每行都写成了配置值 —— 库里 512 维的降级向量被标成 `BAAI/bge-m3`。
    这一列存在的意义就是"将来按模型重算"，记错等于把它废掉。
    """
    from app.tasks import embedding_tasks as et

    class _FakeService:
        loaded_model_name = "BAAI/bge-small-zh-v1.5"  # 降级模型

    monkeypatch.setattr(et, "_get_embedding_service", lambda: _FakeService())
    got = et._model_name()
    assert got == "BAAI/bge-small-zh-v1.5", (
        f"记成了 {got!r} —— 应该是实际加载的降级模型，不是配置里的主模型"
    )


def test_model_name_falls_back_when_model_not_loaded(monkeypatch):
    """模型还没加载时（`loaded_model_name` 为 None）退回配置值，而不是写 None"""
    from app.tasks import embedding_tasks as et

    class _FakeService:
        loaded_model_name = None

    monkeypatch.setattr(et, "_get_embedding_service", lambda: _FakeService())
    assert et._model_name(), "退回值不能是空 —— 那会让 embedding_model 列变成 NULL"


class TestIsolationFirstPool:
    """候选池 = **还没有任何关系的卡片**（2026-09-26 第 3 步）

    ## 为什么这一组必须存在

    以前候选是"最新 200 张"，于是同一批卡片被**反复比较**，而 904 张孤点
    （占孤点的 85.2%）**永远轮不到**。现在候选是"一条边都没有的卡"，
    每处理一批它们就不再是孤点，**下一次点击自然推进到下一批** ——
    不需要额外的游标，孤点数本身就是进度。

    这一组锁的正是"推进"与"不重复处理"这两件事。
    """

    @pytest.mark.asyncio
    async def test_second_run_does_not_reprocess_cards_that_got_edges(
        self, test_db, monkeypatch
    ):
        """★ 已经有边的卡**不再被处理** —— 这是"推进"的另一面

        第一次跑完 c1-c2 就有边了；第二次跑时它们不在候选里，
        所以不会出现"同一对又被建议一次"。c3 是唯一的孤点，但它够不到下界，
        因此第二次应当返回 0。
        """
        from app.services import graph_service

        await _seed(
            test_db,
            notes=["n1", "n2", "n3"],
            cards=[
                ("c1", "n1", "第一章", "甲"),
                ("c2", "n2", "第一章", "乙"),
                ("c3", "n3", "第一章", "丙"),  # 与甲乙都不像
            ],
            vectors={"甲": _vec(0), "乙": _vec(10), "丙": _vec(90)},
        )
        first = await _suggest(test_db)
        assert first == 1, f"第一轮应当建 1 条边，实际 {first}"
        assert await _pairs(test_db) == {("c1", "c2")}

        # 第二轮：c1/c2 有边了，只剩 c3 一个孤点，而它够不到下界
        monkeypatch.setattr(graph_service, "MAX_CARDS_FOR_SUGGEST", 10)
        second = await _suggest(test_db)
        assert second == 0, f"第二轮又建了 {second} 条 —— 有边的卡被重复处理了"
        assert await _pairs(test_db) == {("c1", "c2")}, "关系集合被改动了"

    @pytest.mark.asyncio
    async def test_batch_limit_advances_across_runs(self, test_db, monkeypatch):
        """★ 候选上限真的分批：每轮只处理 N 张，下一轮自动接上

        两组互不相干的卡片（组内相似、组间不相似），把上限压到 1 ——
        于是每轮只能救一张，需要三轮才走完。
        """
        from app.services import graph_service

        await _seed(
            test_db,
            notes=["n1", "n2", "n3", "n4"],
            cards=[
                ("c1", "n1", "第一章", "甲"),
                ("c2", "n2", "第一章", "乙"),
                ("c3", "n3", "第一章", "丙"),
                ("c4", "n4", "第一章", "丁"),
            ],
            # 甲乙一组（0° vs 10°），丙丁一组（90° vs 100°），组间余弦 0
            vectors={"甲": _vec(0), "乙": _vec(10), "丙": _vec(90), "丁": _vec(100)},
        )
        monkeypatch.setattr(graph_service, "MAX_CARDS_FOR_SUGGEST", 1)

        assert await _suggest(test_db) == 1, "第 1 轮应当只救 1 张"
        assert len(await _pairs(test_db)) == 1
        assert await _suggest(test_db) == 1, "第 2 轮没有接上下一批"
        assert len(await _pairs(test_db)) == 2
        assert await _suggest(test_db) == 0, "第 3 轮应当没有孤点可救了"

    @pytest.mark.asyncio
    async def test_no_isolated_cards_returns_zero(self, test_db):
        """没有任何孤点时直接返回 0（不是报错）"""
        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[("c1", "n1", "第一章", "甲"), ("c2", "n2", "第一章", "乙")],
            vectors={"甲": _vec(0), "乙": _vec(10)},
        )
        assert await _suggest(test_db) == 1
        assert await _suggest(test_db) == 0, "第二轮应当没有候选"

    @pytest.mark.asyncio
    async def test_candidate_can_reach_a_neighbor_outside_its_own_batch(
        self, test_db, monkeypatch
    ):
        """★ 非对称比较：孤点的最近邻**可以在这批候选之外**

        这是"候选少、目标全量"的意义所在。这里把上限压到 1，
        而 c1 的相似对象 c2 不在同一批里 —— 如果比较只在候选批内做，c1 会一无所获。
        """
        from app.services import graph_service

        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[("c1", "n1", "第一章", "甲"), ("c2", "n2", "第一章", "乙")],
            vectors={"甲": _vec(0), "乙": _vec(10)},
        )
        monkeypatch.setattr(graph_service, "MAX_CARDS_FOR_SUGGEST", 1)
        assert await _suggest(test_db) == 1, (
            "候选批只有 1 张时没能连上批外的卡片 —— 比较被限制在候选批内了"
        )
        assert await _pairs(test_db) == {("c1", "c2")}


class TestVectorColumnMigration:
    """**既有库**必须能通过 ALTER 补上那 4 个向量列

    ⚠️ 用全新临时库测不出迁移：`create_all` 会直接带着新列建表 ——
    那样测的是"模型定义对不对"，而不是"真库能不能升级"。
    这里刻意**先造一个没有这些列的旧库**，再跑真实的 `_migrate_sqlite`
    （与 `test_prompt_version.py:168-239` 同一个套路，那是本项目已有的先例）。
    """

    async def _old_db(self, tmp_path):
        """造一个"向量列引入之前"的库：knowledge_cards 没有那 4 列，且有一行

        ⚠️ 为什么要连 `notes` 一起建：`_migrate_sqlite` 里有**防御性建表**
        （见 `database.py:454` 起），它会在缺表时补出 `projects` / `note_projects`，
        但**不会**补 `notes`。于是"连跑两次"时第二次的 `table_names` 里有了
        `note_projects` 却没有 `notes`，那条孤儿清理语句
        （`DELETE FROM note_projects WHERE note_id NOT IN (SELECT id FROM notes)`）
        就会 `no such table: notes`。

        这在真实库里不会发生（两张表总是同时存在），是本 fixture 造出来的假象 ——
        但既然要测"连跑两次"，就得把库造得像真的。
        """
        import sqlite3

        path = tmp_path / "old_cards.db"
        con = sqlite3.connect(path)
        con.executescript(
            """
            CREATE TABLE notes (
                id VARCHAR NOT NULL PRIMARY KEY,
                user_id VARCHAR NOT NULL,
                title VARCHAR(500) NOT NULL
            );
            CREATE TABLE knowledge_cards (
                id VARCHAR NOT NULL PRIMARY KEY,
                user_id VARCHAR NOT NULL,
                note_id VARCHAR,
                card_type VARCHAR NOT NULL,
                title VARCHAR(500) NOT NULL,
                content TEXT NOT NULL
            );
            INSERT INTO notes (id, user_id, title) VALUES ('n1', 'u1', '历史笔记');
            INSERT INTO knowledge_cards (id, user_id, note_id, card_type, title, content)
                VALUES ('c-old', 'u1', 'n1', 'concept', '历史卡片', '向量列引入之前。');
            """
        )
        con.commit()
        con.close()
        return path

    @pytest.mark.asyncio
    async def test_migration_adds_vector_columns_to_existing_db(self, tmp_path):
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        from app.database import _migrate_sqlite

        path = await self._old_db(tmp_path)
        engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        try:
            async with engine.begin() as conn:
                await _migrate_sqlite(conn)
            async with engine.connect() as conn:
                cols = {
                    row[1] for row in
                    (await conn.execute(text("PRAGMA table_info(knowledge_cards)"))).all()
                }
                missing = {
                    "embedding_model", "embedding_dim", "embedding", "has_embedding"
                } - cols
                assert not missing, (
                    f"真库会缺这些列：{sorted(missing)} —— 关系建议读不到向量"
                )
                # 历史行：**不回填**向量（回填要跑一遍嵌入模型，275 秒，
                # 不该塞在启动路径里），但 `has_embedding` 必须是 0 而不是 NULL
                row = (await conn.execute(text(
                    "SELECT embedding, has_embedding FROM knowledge_cards WHERE id='c-old'"
                ))).one()
                assert row[0] is None, "历史行被回填了向量 —— 迁移不该做这件事"
                assert row[1] == 0, (
                    f"has_embedding 应为 0（NOT NULL DEFAULT 0），实际 {row[1]!r}"
                )
                # 取数路径要走的索引
                idx = {
                    r[1] for r in
                    (await conn.execute(text("PRAGMA index_list(knowledge_cards)"))).all()
                }
                assert "ix_knowledge_cards_user_embed" in idx, (
                    f"缺索引 ix_knowledge_cards_user_embed，实际：{sorted(idx)}"
                )
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_migration_is_idempotent(self, tmp_path):
        """连跑两次不能报错（启动路径上每次都会跑一遍）"""
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        from app.database import _migrate_sqlite

        path = await self._old_db(tmp_path)
        engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        try:
            for _ in range(2):
                async with engine.begin() as conn:
                    await _migrate_sqlite(conn)
            async with engine.connect() as conn:
                cols = {
                    row[1] for row in
                    (await conn.execute(text("PRAGMA table_info(knowledge_cards)"))).all()
                }
                assert "embedding" in cols
        finally:
            await engine.dispose()
