"""
RAG 检索层行为回归测试（overhaul-plan 阶段 2.6 / 2.10）

## 这个文件为什么存在

`test_week5_6_understanding.py::TestRAGService` 只有三个"类存在 / 方法存在 /
是 async"的断言 —— 它们**在实现完全错误时也会通过**。检索层是本项目产品承诺
（"基于你的资料回答，可追溯"）的唯一承载，却几乎没有行为断言。

本文件锁住阶段 2 里两处**删除类**改动，删除的回归风险特别高：
代码被删掉了，没有测试守着，"将来某次重构顺手加回来"是很容易发生的事，
而加回来之后没人会记得当初为什么删。

- 2.6 删除 n-gram 检索通道（与 BM25 语料完全相同、打分更粗糙，却拿同等 RRF 权重）
- 2.10 删除 `_kb_cache` 模块级无界缓存（内存随用户无界增长、缓存 ORM 实例、
  正确性靠 5 处调用方记得失效）
"""

import pytest

from app.models.knowledge_card import CardType, KnowledgeCard
from app.models.note import Note, NoteStatus, SourceType
from app.models.user import User
from app.services.rag_service import RAGService


class TestNGramChannelRemoved:
    """阶段 2.6：n-gram 通道必须保持删除状态"""

    def test_no_ngram_search_method(self):
        """`_search_relevant_cards`（n-gram 通道）不得存在

        它与 BM25 的语料**完全相同**（都来自 `_get_user_cards`），
        打分只是"命中子串就累加子串长度"= 未归一化的词频计数，
        没有 IDF 加权也没有长度归一化。RRF 却给它同等权重，
        于是它的实际作用是把噪声顶进 top-5。
        """
        service = RAGService()
        assert not hasattr(service, "_search_relevant_cards"), (
            "n-gram 检索通道被重新引入。它的语料与 BM25 完全相同、打分更粗糙，"
            "却会拿到同等的 RRF 权重 —— 恢复它等于恢复一路纯噪声。"
        )

    def test_rrf_fusion_takes_exactly_two_result_lists(self):
        """`_rrf_fusion` 只融合两路（向量 + BM25）

        参数个数即契约：多一路就会改变融合语义。
        """
        import inspect

        params = list(inspect.signature(RAGService._rrf_fusion).parameters)
        # 静态方法签名：vector_results, bm25_results, k, top_k
        assert params == ["vector_results", "bm25_results", "k", "top_k"], (
            f"RRF 融合的参数列表已变化: {params}。"
            "若新增了检索通道，请先补一份能证明它带来增益的评测（阶段 2.9）。"
        )

    def test_no_ngram_weight_in_fusion(self):
        """融合结果不得因 n-gram 产生第三个来源

        直接验证行为：同一个文档同时出现在两路里，其融合分数应当等于
        两路 rank 贡献之和；若存在隐含的第三路，分数会更高。
        """
        doc = {"note_id": "n1", "content": "内容", "note_title": None, "block_index": 0}
        fused = RAGService._rrf_fusion([doc], [doc], k=60, top_k=5)
        assert len(fused) == 1
        # 两路各 rank 0 → 1/61 + 1/61
        assert fused[0]["similarity"] == pytest.approx(2 / 61), (
            f"融合分数异常: {fused[0]['similarity']}（预期 2/61 ≈ 0.0328）"
        )

    def test_dedupe_key_merges_same_doc_across_channels(self):
        """同一个文档出现在两路时必须合并，而不是占两个 top-k 名额

        去重键是 (note_id, content[:200])。若去重失效，同一段内容会在
        上下文里出现两次，白白挤掉一个本可以不同的结果。
        """
        a = {"note_id": "n1", "content": "同样的内容", "note_title": "标题", "block_index": 0}
        b = {"note_id": "n1", "content": "同样的内容", "note_title": None, "block_index": 0}
        fused = RAGService._rrf_fusion([a], [b], k=60, top_k=5)
        assert len(fused) == 1, "同一文档在两路中未被去重"

    def test_fusion_orders_by_accumulated_score(self):
        """两路都排第一的文档必须胜过只在一路排第一的文档"""
        both = {"note_id": "both", "content": "两路都有", "note_title": None, "block_index": 0}
        only_one = {"note_id": "one", "content": "只有一路", "note_title": None, "block_index": 0}
        fused = RAGService._rrf_fusion([both, only_one], [both], k=60, top_k=5)
        assert fused[0]["note_id"] == "both"


class TestNoUnboundedCache:
    """阶段 2.10：模块级卡片缓存必须保持删除状态"""

    def test_no_module_level_kb_cache(self):
        """`_kb_cache` 不得存在

        原实现是 `Dict[str, tuple[float, List[KnowledgeCard]]]`：60 秒 TTL、
        **无容量上界**、缓存完整 ORM 实例。实测本项目单用户 1183 张卡片，
        多用户即线性叠加且永不主动回收。
        """
        from app.services import rag_service

        assert not hasattr(rag_service, "_kb_cache"), (
            "无界卡片缓存被重新引入。它是模块级字典，内存随用户数增长且没有上界；"
            "若确实需要缓存，必须同时给出容量上界与淘汰策略。"
        )

    def test_no_invalidate_cache_indirection(self):
        """`invalidate_kb_cache` 不得存在

        它的存在意味着"正确性依赖 5 处调用方记得调用"：
        卡片增删、笔记 purge、理解流程、联合分析、拓展生成。
        漏掉任何一处，用户就会在 TTL 内看到已删除的卡片参与问答。
        这类"靠约定维持正确性"的设计在本项目已经导致过真实库被测试写入。
        """
        from app.services import rag_service

        assert not hasattr(rag_service, "invalidate_kb_cache"), (
            "缓存失效入口被重新引入 —— 说明缓存又回来了。"
        )

    def test_callers_do_not_reference_removed_cache(self):
        """所有曾调用 `invalidate_kb_cache` 的模块都不得再引用它

        静态检查而非逐个 import：漏改一处就是一个 ImportError，
        而 ImportError 只在那个端点被访问时才暴露。
        """
        import pathlib

        backend = pathlib.Path(__file__).resolve().parent.parent
        offenders = []
        for py in (backend / "app").rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            if "invalidate_kb_cache" in text or "_kb_cache" in text:
                # rag_service 的注释里会提到旧名字（解释为什么删掉），允许
                if py.name == "rag_service.py":
                    continue
                offenders.append(str(py.relative_to(backend)))
        assert not offenders, f"仍引用已删除的缓存接口: {offenders}"


@pytest.mark.asyncio
class TestCardCorpus:
    """`_get_user_cards`：语料查询的正确性（不再有缓存层）"""

    async def test_returns_plain_dicts_not_orm_instances(self, test_db):
        """必须返回普通 dict，不能是 ORM 实例

        原实现缓存 ORM 实例，与 session 生命周期绑定 ——
        session 关闭后访问未加载属性会抛 `DetachedInstanceError`，
        属于"平时不出现、并发时偶发"的失败模式。
        """
        uid, note_id = await _make_user_and_note(test_db)
        await _add_card(test_db, uid, note_id, "卡片标题", "卡片内容")

        service = RAGService()
        service._session_factory = test_db
        cards = await service._get_user_cards(uid)

        assert len(cards) == 1
        assert isinstance(cards[0], dict), f"返回了 {type(cards[0])}，应为 dict"
        assert set(cards[0]) == {"card_id", "note_id", "title", "content", "chapter_title"}
        assert cards[0]["title"] == "卡片标题"

    async def test_excludes_cards_of_trashed_notes(self, test_db):
        """回收站笔记的卡片不得进入 QA 语料

        否则用户删掉的资料仍然会被问答引用 —— 既违反直觉，
        也可能把用户主动清理的内容重新"答"出来。
        """
        from datetime import datetime

        uid, note_id = await _make_user_and_note(test_db)
        await _add_card(test_db, uid, note_id, "回收站的卡片", "不应被检索到")

        async with test_db() as db:
            note = (await db.execute(_select_note(note_id))).scalar_one()
            note.trashed_at = datetime.utcnow()
            await db.commit()

        service = RAGService()
        service._session_factory = test_db
        cards = await service._get_user_cards(uid)

        assert cards == [], f"回收站笔记的卡片仍在语料中: {[c['title'] for c in cards]}"

    async def test_keeps_standalone_cards(self, test_db):
        """独立卡片（`note_id` 为 NULL）必须保留

        物理删除笔记时勾选"提升核心卡片"会把 `note_id` 置 NULL，
        卡片成为图谱独立节点 —— 它们仍是用户的资料，不能一起丢掉。
        """
        uid, _note_id = await _make_user_and_note(test_db)
        async with test_db() as db:
            db.add(KnowledgeCard(
                user_id=uid, note_id=None, card_type=CardType.concept,
                title="独立卡片", content="提升出来的核心知识点",
            ))
            await db.commit()

        service = RAGService()
        service._session_factory = test_db
        cards = await service._get_user_cards(uid)

        assert [c["title"] for c in cards] == ["独立卡片"]

    async def test_isolated_per_user(self, test_db):
        """跨用户语料必须隔离（不得把别人的卡片喂进问答）"""
        uid_a, note_a = await _make_user_and_note(test_db)
        uid_b, note_b = await _make_user_and_note(test_db)
        await _add_card(test_db, uid_a, note_a, "A 的卡片", "A 的内容")
        await _add_card(test_db, uid_b, note_b, "B 的卡片", "B 的内容")

        service = RAGService()
        service._session_factory = test_db
        cards = await service._get_user_cards(uid_a)

        assert [c["title"] for c in cards] == ["A 的卡片"], (
            "语料未按 user_id 隔离"
        )


class TestBM25Tokenizer:
    """`_tokenize`：中文 2-gram + 英文小写切分"""

    def test_chinese_uses_bigram(self):
        assert RAGService._tokenize("水电站") == ["水电", "电站"]

    def test_english_is_lowercased_and_split(self):
        assert RAGService._tokenize("Hello World") == ["hello", "world"]

    def test_mixed_content_keeps_both(self):
        tokens = RAGService._tokenize("500kV 变电站")
        assert "500kv" in tokens
        assert "变电" in tokens

    def test_empty_input(self):
        assert RAGService._tokenize("") == []

    def test_single_chinese_char_yields_no_bigram(self):
        """单个汉字产生不了 bigram —— 这是该分词的已知局限

        记录成断言而不是留白：若将来为此改成分词器（阶段 2′ 的 FTS5 路线），
        这条测试会失败并提醒"行为确实变了"。
        """
        assert RAGService._tokenize("水") == []


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _select_note(note_id: str):
    from sqlalchemy import select

    return select(Note).where(Note.id == note_id)


async def _make_user_and_note(session_factory) -> tuple[str, str]:
    """建用户 + 笔记并**按依赖顺序**显式落库

    用 Core insert 而不是 ORM `db.add`：`notes` 上有两条外键
    （`user_id -> users.id`、`folder_id -> folders.id`）。
    同一个 session 里 add 两个对象时，flush 的实际顺序决定了是否撞外键，
    而那是 SQLAlchemy 的内部行为 —— 测试不该依赖它。
    显式先插 users、提交，再插 notes，语义明确且稳定。
    """
    import uuid

    from sqlalchemy import insert

    uid = str(uuid.uuid4())
    nid = str(uuid.uuid4())
    async with session_factory() as db:
        await db.execute(insert(User).values(
            id=uid, email=f"{uid[:8]}@example.com", username=f"u{uid[:8]}",
            hashed_password="x", is_active=True,
        ))
        await db.execute(insert(Note).values(
            id=nid, user_id=uid, title=f"笔记 {nid[:6]}",
            source_type=SourceType.pdf.value, status=NoteStatus.cleaned.value,
        ))
        await db.commit()
    return uid, nid


async def _add_card(
    session_factory, user_id: str, note_id: str, title: str, content: str,
) -> None:
    from sqlalchemy import insert

    async with session_factory() as db:
        await db.execute(insert(KnowledgeCard).values(
            user_id=user_id, note_id=note_id, card_type=CardType.concept.value,
            title=title, content=content,
        ))
        await db.commit()
