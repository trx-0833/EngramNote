"""阶段 4.8 / 4.9：卡片入库门（重跑幂等 + 质量门）测试

## 这份测试要证明什么

**核心那条**是 `TestIdempotentIntake::test_rerun_does_not_duplicate_cards`：
把同一批知识点入库两次，卡片数必须不变。

改造前那行代码是 `db.add(KnowledgeCard(...))` —— 无条件插入。
也就是说"重新理解这篇笔记"按一次，这篇笔记的卡片就翻一倍，
而重复卡片会进复习队列（两道一模一样的题）、进知识图谱（孪生节点）。

⚠️ 真库实测（2026-09-11）：**跨多天创建过卡片的笔记数为 0**，
完全重复的 `(title, content)` 也是 0 —— 也就是说这个缺陷
**在真库里还没有发作过**（没人按过重跑）。所以这份测试是
**预防性**的：它证明的是"按下去不会坏"，不是"修好了一个已在发生的问题"。
"""

import uuid
from datetime import datetime

import pytest
from sqlalchemy import func, select

from app.models.knowledge_card import KnowledgeCard
from app.models.note import Note, SourceType
from app.models.user import User
from app.services.card_intake_service import (
    GateConfig,
    card_content_hash,
    normalize_card_text,
    reject_reason,
    save_cards_idempotent,
)

CHAPTER = {"chapter_title": "第一章", "chapter_index": 1}


def point(title="浮充", content="蓄电池的一种运行方式，端电压保持恒定。", **kw):
    base = {"title": title, "content": content, "card_type": "concept",
            "source_text": "浮充是指蓄电池的一种运行方式。"}
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# 指纹
# ---------------------------------------------------------------------------

class TestContentHash:
    def test_is_stable_and_order_sensitive(self):
        """同内容同哈希；标题与正文互换必须得到不同哈希（否则两张卡会互相顶掉）"""
        a = card_content_hash("甲", "乙")
        assert a == card_content_hash("甲", "乙")
        assert a != card_content_hash("乙", "甲")

    def test_normalization_ignores_layout(self):
        """换行/多空格/全角差异不算"不同的卡" —— 否则重跑时同张卡会被判成新卡"""
        assert card_content_hash("浮充", "甲\n乙") == card_content_hash("浮充", "甲 乙")
        assert card_content_hash("浮充", "甲  乙") == card_content_hash("浮充", "甲 乙")
        assert card_content_hash("浮充", "甲\n\n乙") == card_content_hash("浮充", "甲 乙")
        # NFKC：全角括号与半角括号归一
        assert normalize_card_text("（甲）") == normalize_card_text("(甲)")

    def test_normalization_does_not_delete_whitespace(self):
        """★ 只**压缩**空白，不**删除**空白

        `"甲乙"` 与 `"甲 乙"` 必须保持不同：英文里空格是词边界，
        全删了会让 "machine learning" 与 "machinelearning" 变成同一张卡。
        中文排版里也确实存在"该不该有空格"的差异，但那属于内容差异，
        不是排版差异 —— 判错了方向是**合并两张不同的卡**，比漏判更糟。
        """
        assert normalize_card_text("甲 乙") == "甲 乙"
        assert card_content_hash("浮充", "甲乙") != card_content_hash("浮充", "甲 乙")

    def test_does_not_do_fuzzy_matching(self):
        """★ 不做模糊匹配：这是"同一张卡"的判定，不是"两张卡很像"

        把相似度判定混进来，重跑时就会**误删**内容相近但不同的卡片。
        模糊去重的职责在 `detect_card_duplicates`。
        """
        assert card_content_hash("浮充", "甲") != card_content_hash("浮充", "甲。")
        assert card_content_hash("浮充", "端电压恒定") != card_content_hash("浮充", "端电压保持恒定")

    def test_empty_is_still_a_hash(self):
        """空内容也给哈希（是否入库由质量门决定，两件事分开）"""
        assert len(card_content_hash("", "")) == 32


# ---------------------------------------------------------------------------
# 质量门
# ---------------------------------------------------------------------------

class TestQualityGate:
    def test_clean_card_passes(self):
        assert reject_reason(point(), GateConfig()) is None

    def test_empty_or_short_content_is_rejected(self):
        """★ 正文过短直接拒 —— 它一旦入库就再也没法自动清理（本模块不删卡片）"""
        cfg = GateConfig()
        assert reject_reason(point(content=""), cfg)
        assert reject_reason(point(content="短"), cfg)
        # 恰好 10 字通过（边界包含）
        assert reject_reason(point(content="刚好十个字的内容内容"), cfg) is None

    def test_missing_title_is_rejected_not_renamed(self):
        """★ 标题缺失要**拒**，而不是填一个"未命名知识点"

        占位标题会让一张本该丢弃的脏卡片看起来像正常卡片，
        而它入库后就再也没法自动清掉了。
        """
        assert reject_reason(point(title=""), GateConfig())
        assert reject_reason(point(title=" "), GateConfig())
        assert reject_reason(point(title=None), GateConfig())

    def test_missing_source_text_is_rejected_by_default(self):
        """无原文出处 → 拒（引用回跳在这些卡上不可用）"""
        cfg = GateConfig()
        assert reject_reason(point(source_text=""), cfg)
        assert reject_reason(point(source_text="   "), cfg)
        # 可配置关闭：代价是引用回跳不可用，这是使用方的选择
        assert reject_reason(point(source_text=""), GateConfig(require_source_text=False)) is None

    def test_reason_is_human_readable(self):
        """原因要能直接进日志与统计 —— 只报"被拒了"没有用，"因为什么"才有用"""
        reason = reject_reason(point(content="短"), GateConfig())
        assert "正文过短" in reason and "10" in reason

    def test_thresholds_come_from_config(self):
        cfg = GateConfig(min_content_chars=50)
        assert reject_reason(point(content="只有二十来个字的内容内容内容"), cfg)


# ---------------------------------------------------------------------------
# 入库与幂等
# ---------------------------------------------------------------------------

async def _make_note(session_factory) -> tuple[str, str]:
    uid = str(uuid.uuid4())
    nid = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(User(id=uid, email=f"{uid[:8]}@e.com", username=f"u{uid[:8]}",
                    hashed_password="x", is_active=True))
        await db.commit()
    async with session_factory() as db:
        db.add(Note(id=nid, user_id=uid, title="t", source_type=SourceType.pdf))
        await db.commit()
    return uid, nid


async def _card_count(session_factory, note_id: str) -> int:
    async with session_factory() as db:
        return (await db.execute(
            select(func.count()).select_from(KnowledgeCard)
            .where(KnowledgeCard.note_id == note_id)
        )).scalar() or 0


@pytest.mark.asyncio
class TestIdempotentIntake:
    async def test_first_run_creates_cards(self, test_db):
        uid, nid = await _make_note(test_db)
        async with test_db() as db:
            outcome = await save_cards_idempotent(
                db, uid, nid, CHAPTER, "摘要", [point(), point(title="均充", content="另一种充电方式，电流恒定。")],
            )
        assert len(outcome.created) == 2
        assert outcome.reused == []
        assert await _card_count(test_db, nid) == 2

    async def test_rerun_does_not_duplicate_cards(self, test_db):
        """★★ 本文件的核心：同一批知识点入库两次，卡片数必须不变

        改造前是无条件 `db.add(...)`：按一次"重新理解"，卡片翻一倍。
        重复卡片会进复习队列（两道一模一样的题）与知识图谱（孪生节点），
        而用户很难理解为什么。
        """
        uid, nid = await _make_note(test_db)
        points = [
            point(),
            point(title="均充", content="另一种充电方式，电流恒定。"),
            point(title="浮充电压", content="浮充时单体电压通常在 2.23V 左右。"),
        ]

        async with test_db() as db:
            first = await save_cards_idempotent(db, uid, nid, CHAPTER, "摘要", points)
        assert len(first.created) == 3
        assert await _card_count(test_db, nid) == 3

        # 重跑：同样的输入
        async with test_db() as db:
            second = await save_cards_idempotent(db, uid, nid, CHAPTER, "摘要", points)

        assert len(second.created) == 0, "重跑又插入了卡片 —— 幂等失效"
        assert len(second.reused) == 3, "重跑没有识别出已有的卡片"
        assert await _card_count(test_db, nid) == 3, "重跑把卡片翻倍了"

    async def test_rerun_only_adds_the_delta(self, test_db):
        """"只补差集"：重跑时多出来的新知识点照常入库，已有的复用"""
        uid, nid = await _make_note(test_db)
        async with test_db() as db:
            await save_cards_idempotent(db, uid, nid, CHAPTER, "摘要", [point()])

        async with test_db() as db:
            outcome = await save_cards_idempotent(
                db, uid, nid, CHAPTER, "摘要",
                [point(), point(title="新卡", content="重跑时才抽出来的知识点内容。")],
            )
        assert len(outcome.created) == 1
        assert len(outcome.reused) == 1
        assert await _card_count(test_db, nid) == 2

    async def test_existing_cards_are_never_deleted(self, test_db):
        """★ "只补差集"意味着**不删除**

        旧卡片上挂着 `review_states`（唯一约束含 item_id）。删卡片就等于
        把用户的复习进度变成孤儿行 —— 那正是症状 D-3 的形态。
        宁可留下几张用户不再需要的卡，也不能让学习记录凭空消失。
        """
        uid, nid = await _make_note(test_db)
        async with test_db() as db:
            await save_cards_idempotent(
                db, uid, nid, CHAPTER, "摘要",
                [point(), point(title="会被淘汰的卡", content="这次抽出来，下次没有。")],
            )
        assert await _card_count(test_db, nid) == 2

        async with test_db() as db:
            outcome = await save_cards_idempotent(db, uid, nid, CHAPTER, "摘要", [point()])

        assert outcome.created == []
        assert await _card_count(test_db, nid) == 2, "旧卡片被删掉了"

    async def test_duplicates_inside_one_batch(self, test_db):
        """同一批里重复出现（LLM 偶尔会把一个知识点写两遍）也只入库一张"""
        uid, nid = await _make_note(test_db)
        async with test_db() as db:
            outcome = await save_cards_idempotent(
                db, uid, nid, CHAPTER, "摘要", [point(), point()],
            )
        assert len(outcome.created) == 1
        assert len(outcome.reused) == 1

    async def test_layout_difference_is_still_the_same_card(self, test_db):
        """排版差异（换行 vs 空格）不该被判成新卡 —— 这是规范化存在的理由

        真实场景：重跑时模型把原来的换行写成了空格（或反之）。
        两者规范化后相同，因此复用而不是新插一张。
        """
        uid, nid = await _make_note(test_db)
        async with test_db() as db:
            await save_cards_idempotent(
                db, uid, nid, CHAPTER, "摘要",
                [point(content="蓄电池的一种运行方式，\n端电压保持恒定。")],
            )
        async with test_db() as db:
            outcome = await save_cards_idempotent(
                db, uid, nid, CHAPTER, "摘要",
                [point(content="蓄电池的一种运行方式，   端电压保持恒定。")],
            )
        assert outcome.created == []
        assert await _card_count(test_db, nid) == 1

    async def test_same_card_in_another_note_is_not_reused(self, test_db):
        """★ 去重范围是**同一篇笔记**：不同笔记里的同名卡片是两张卡

        跨笔记去重会让"同一概念在不同资料里的不同阐述"互相顶掉，
        而它们的出处、上下文都不同。
        """
        uid, nid = await _make_note(test_db)
        other_nid = str(uuid.uuid4())
        async with test_db() as db:
            db.add(Note(id=other_nid, user_id=uid, title="t2", source_type=SourceType.pdf))
            await db.commit()

        async with test_db() as db:
            await save_cards_idempotent(db, uid, nid, CHAPTER, "摘要", [point()])
        async with test_db() as db:
            outcome = await save_cards_idempotent(db, uid, other_nid, CHAPTER, "摘要", [point()])

        assert len(outcome.created) == 1, "跨笔记去重了 —— 不同出处的卡片被顶掉"
        assert await _card_count(test_db, nid) == 1
        assert await _card_count(test_db, other_nid) == 1

    async def test_hash_is_persisted_so_next_run_can_match(self, test_db):
        """指纹必须落库 —— 现算的话重跑无法走索引，只能全表读正文"""
        uid, nid = await _make_note(test_db)
        async with test_db() as db:
            await save_cards_idempotent(db, uid, nid, CHAPTER, "摘要", [point()])
        async with test_db() as db:
            card = (await db.execute(
                select(KnowledgeCard).where(KnowledgeCard.note_id == nid)
            )).scalars().first()
        assert card.content_hash == card_content_hash(card.title, card.content)


@pytest.mark.asyncio
class TestGateInIntake:
    async def test_rejected_cards_are_reported_not_silently_dropped(self, test_db):
        """★ 被拒的卡片必须出现在结果里

        只进日志的话，"脏卡片不入库"就变成了"静默少了几张卡" ——
        而用户只会看到卡片比预期少，不知道是被门拦了还是模型没抽出来。

        ## 两层拒绝，两类原因（阶段 4.1 收尾加入结构化校验后）

        | 输入 | 谁拒的 | 报告的原因 |
        |---|---|---|
        | 字段**缺失/空白**（无正文、无标题） | 结构化校验（Pydantic） | `缺少必填文本字段: content` |
        | 字段**存在但过短** | 质量门（4.9） | `正文过短` / `标题过短` |

        分层是有意的：校验回答"这条数据形状对不对"，门回答"内容够不够格"。
        以前两者都由门回答（空正文报"正文过短"），排查时会被引向"内容太短"
        而不是"模型压根没给这个字段"。
        """
        uid, nid = await _make_note(test_db)
        async with test_db() as db:
            outcome = await save_cards_idempotent(
                db, uid, nid, CHAPTER, "摘要",
                [
                    point(),
                    point(title="空正文", content=""),
                    point(title="", content="标题缺失的卡片内容内容内容"),
                    # 字段都不缺，只是太短 → 这一条应当由质量门拦下
                    point(title="短", content="也短"),
                ],
            )
        assert len(outcome.created) == 1
        assert len(outcome.rejected) == 3
        reasons = " | ".join(outcome.rejected_reasons)
        assert "content" in reasons, f"缺少正文的条目没有被报告: {reasons}"
        assert "title" in reasons, f"缺少标题的条目没有被报告: {reasons}"
        assert "正文过短" in reasons or "标题过短" in reasons, (
            f"字段齐全但过短的条目没有走到质量门: {reasons}"
        )

    async def test_rejected_cards_do_not_take_a_hash_slot(self, test_db):
        """被拒的卡不留任何痕迹：修好之后再抽出来应当能正常入库"""
        uid, nid = await _make_note(test_db)
        async with test_db() as db:
            await save_cards_idempotent(
                db, uid, nid, CHAPTER, "摘要", [point(content="短")],
            )
        assert await _card_count(test_db, nid) == 0

        async with test_db() as db:
            outcome = await save_cards_idempotent(db, uid, nid, CHAPTER, "摘要", [point()])
        assert len(outcome.created) == 1

    async def test_per_run_cap_limits_new_cards(self, test_db):
        """★ 单次抽取上限只约束**新建**，且超限是**报告**出来的

        一次跑飞的抽取不该灌进来几千张卡；但也不能静默丢弃 ——
        用户必须能知道"还有 N 张没入库"，否则会以为模型只抽出了这么多。
        """
        uid, nid = await _make_note(test_db)
        points = [
            point(title=f"知识点{i}", content=f"这是第 {i} 个知识点的正文内容。")
            for i in range(10)
        ]
        async with test_db() as db:
            outcome = await save_cards_idempotent(
                db, uid, nid, CHAPTER, "摘要", points,
                config=GateConfig(max_new_cards_per_run=3),
            )
        assert len(outcome.created) == 3
        assert outcome.truncated == 7
        assert await _card_count(test_db, nid) == 3

    async def test_cap_does_not_block_reruns(self, test_db):
        """★ 上限不能挡住重跑：复用不计入上限

        否则"卡片超过上限的笔记"将永远无法通过重跑补差集。
        """
        uid, nid = await _make_note(test_db)
        points = [
            point(title=f"知识点{i}", content=f"这是第 {i} 个知识点的正文内容。")
            for i in range(5)
        ]
        cfg = GateConfig(max_new_cards_per_run=5)
        async with test_db() as db:
            await save_cards_idempotent(db, uid, nid, CHAPTER, "摘要", points, config=cfg)

        async with test_db() as db:
            outcome = await save_cards_idempotent(
                db, uid, nid, CHAPTER, "摘要", points, config=cfg,
            )
        assert len(outcome.reused) == 5
        assert outcome.truncated == 0

    async def test_already_created_counts_toward_the_cap(self, test_db):
        """跨章节累计：上限是"一次理解"的总量，不是"每个章节"的量"""
        uid, nid = await _make_note(test_db)
        cfg = GateConfig(max_new_cards_per_run=3)
        async with test_db() as db:
            first = await save_cards_idempotent(
                db, uid, nid, CHAPTER, "摘要",
                [point(title=f"a{i}", content=f"第一章第 {i} 张卡片的正文。") for i in range(2)],
                config=cfg,
            )
        async with test_db() as db:
            second = await save_cards_idempotent(
                db, uid, nid, {"chapter_title": "第二章", "chapter_index": 2}, "摘要",
                [point(title=f"b{i}", content=f"第二章第 {i} 张卡片的正文。") for i in range(3)],
                config=cfg, already_created=len(first.created),
            )
        assert len(second.created) == 1, "跨章节没有累计，上限形同虚设"
        assert second.truncated == 2

    async def test_card_type_falls_back_to_concept(self, test_db):
        """非法 card_type 退回 concept（既有行为，别在重构中丢掉）"""
        uid, nid = await _make_note(test_db)
        async with test_db() as db:
            await save_cards_idempotent(
                db, uid, nid, CHAPTER, "摘要", [point(card_type="不存在的类型")],
            )
        async with test_db() as db:
            card = (await db.execute(
                select(KnowledgeCard).where(KnowledgeCard.note_id == nid)
            )).scalars().first()
        assert card.card_type.value == "concept"

    async def test_source_text_is_truncated(self, test_db):
        """出处截断到 5000 字（既有行为）"""
        uid, nid = await _make_note(test_db)
        async with test_db() as db:
            await save_cards_idempotent(
                db, uid, nid, CHAPTER, "摘要", [point(source_text="甲" * 9000)],
            )
        async with test_db() as db:
            card = (await db.execute(
                select(KnowledgeCard).where(KnowledgeCard.note_id == nid)
            )).scalars().first()
        assert len(card.source_text) == 5000

    async def test_created_cards_have_ids_and_timestamps(self, test_db):
        """入库后必须 refresh（否则调用方拿到的 id 是 None）"""
        uid, nid = await _make_note(test_db)
        async with test_db() as db:
            outcome = await save_cards_idempotent(db, uid, nid, CHAPTER, "摘要", [point()])
        assert outcome.created[0].id
        assert outcome.created[0].created_at is not None
        assert isinstance(outcome.created[0].created_at, datetime)
