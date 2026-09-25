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


class TestChapterGranularity:
    """阶段 1：只跳过同章节，不再跳过整篇笔记"""

    @pytest.mark.asyncio
    async def test_same_note_cross_chapter_pair_is_kept(self, test_db, monkeypatch):
        """★ 核心回归：同笔记**跨章节**的卡片对必须建边

        旧规则判 `note_id` 相同就跳过 —— 这条用例在旧规则下必然失败。
        """
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
            "同笔记跨章节的卡片对被丢掉了 —— 判据必须用 (note_id, chapter_title)，"
            "不能只用 note_id（实测它占被丢弃对的 89.5%，且相似度比跨笔记还高）"
        )

    @pytest.mark.asyncio
    async def test_same_note_same_chapter_pair_is_skipped(self, test_db, monkeypatch):
        """同笔记**同章节**必须跳过（章节内才是真噪音源：余弦中位 0.667）"""
        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[
                ("c1", "n1", "第一章", "甲"),
                ("c2", "n1", "第一章", "乙"),
                ("c3", "n2", "第一章", "丙"),
            ],
        )
        await _suggest(
            test_db,
            monkeypatch,
            {"甲": _vec(0), "乙": _vec(26), "丙": _vec(-26)},
        )
        pairs = await _pairs(test_db)
        assert ("c1", "c2") not in pairs, "同章节的卡片对被建边了 —— 那是噪音"
        # 反证：同一批里跨笔记的对**必须**建出来，否则上面那条可能只是"什么都没建"
        assert ("c1", "c3") in pairs

    @pytest.mark.asyncio
    async def test_null_chapter_treated_as_same_chapter(self, test_db, monkeypatch):
        """`chapter_title` 为 NULL 的两张同笔记卡片 → 视为同章节，保守跳过

        退化成旧行为（不建边），不会比改动前更糟 —— 这是刻意的兜底。
        """
        await _seed(
            test_db,
            notes=["n1"],
            cards=[
                ("c1", "n1", None, "甲"),
                ("c2", "n1", None, "乙"),
            ],
        )
        await _suggest(test_db, monkeypatch, {"甲": _vec(0), "乙": _vec(26)})
        assert ("c1", "c2") not in await _pairs(test_db)


class TestTopKSelection:
    """阶段 2：每卡 Top-K + 绝对下界，取代「相似度 ≥ 全局阈值」"""

    @pytest.mark.asyncio
    async def test_below_floor_creates_nothing(self, test_db, monkeypatch):
        """低于绝对下界的对不建边（下界是兜底，不是主判据）"""
        from app.services.graph_service import MIN_SIMILARITY

        # 夹角 60° → 余弦 0.5，低于下界 0.55
        assert _sim_to(60) < MIN_SIMILARITY
        await _seed(
            test_db,
            notes=["n1", "n2"],
            cards=[("c1", "n1", "第一章", "甲"), ("c2", "n2", "第一章", "乙")],
        )
        await _suggest(test_db, monkeypatch, {"甲": _vec(0), "乙": _vec(60)})
        assert await _pairs(test_db) == set()

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
