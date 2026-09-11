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

import os

import pytest

from app.models.chunk import Chunk
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

        参数列表即契约：多一路就会改变融合语义。
        `k` 与 `bm25_weight` 可省略（取配置值），所以断言只看前两个位置参数。
        """
        import inspect

        params = list(inspect.signature(RAGService._rrf_fusion).parameters)
        assert params == ["vector_results", "bm25_results", "k", "bm25_weight", "top_k"], (
            f"RRF 融合的参数列表已变化: {params}。"
            "若新增了检索通道，请先补一份能证明它带来增益的评测（阶段 2.9）。"
        )
        # 前两个必须仍是两路结果
        assert params[:2] == ["vector_results", "bm25_results"]

    def test_weight_split_does_not_change_total_when_both_hit(self):
        """两路都命中同一文档时，总分 = 1/(k+1)，**与权重取值无关**

        因为 `(1-w)/(k+1) + w/(k+1) = 1/(k+1)`。这条性质说明：
        加权只影响"两路意见不一致时谁占优"，不影响"两路一致时"的分数 ——
        这正是我们希望加权起到的作用（校正强弱差异，而非放大共识）。
        """
        doc = {"note_id": "n1", "content": "内容", "note_title": None, "block_index": 0}
        single = RAGService._rrf_fusion([doc], [], k=60, bm25_weight=0.0)
        assert single[0]["similarity"] == pytest.approx(1 / 61)

        for w in (0.0, 0.5, 0.65, 1.0):
            both = RAGService._rrf_fusion([doc], [doc], k=60, bm25_weight=w)
            assert both[0]["similarity"] == pytest.approx(1 / 61), (
                f"w={w} 时两路都命中的总分应恒为 1/61"
            )

    def test_bm25_weight_actually_shifts_ranking(self):
        """权重必须真的改变排序（否则配置是装饰）

        构造：两路各有一条**不同**的文档，谁权重高谁排前面。
        """
        vec_doc = {"note_id": "v", "content": "向量独有", "note_title": None, "block_index": 0}
        bm_doc = {"note_id": "b", "content": "BM25独有", "note_title": None, "block_index": 0}

        # 向量权重高 → 向量那条排第一
        heavy_vec = RAGService._rrf_fusion([vec_doc], [bm_doc], k=60, bm25_weight=0.1)
        assert heavy_vec[0]["note_id"] == "v"
        # BM25 权重高 → BM25 那条排第一
        heavy_bm = RAGService._rrf_fusion([vec_doc], [bm_doc], k=60, bm25_weight=0.9)
        assert heavy_bm[0]["note_id"] == "b"

    def test_smaller_k_sharpens_rank_difference(self):
        """k 越小，名次差异越大（k 大是"压平名次"的原因）

        原实现 k=60 时第 1 名（1/61）与第 5 名（1/65）只差 4%，
        导致分数几乎只反映"是否两路同时出现"。这里锁住这个性质，
        避免有人"为了方便"把 k 调回大值。
        """
        docs = [
            {"note_id": f"n{i}", "content": f"内容{i}", "note_title": None, "block_index": 0}
            for i in range(5)
        ]
        def spread(k: int) -> float:
            out = RAGService._rrf_fusion(docs, [], k=k, bm25_weight=0.0)
            s = {d["note_id"]: d["similarity"] for d in out}
            return s["n0"] / s["n4"]

        assert spread(1) > spread(60) * 2, (
            f"k=1 与 k=60 的名次区分度差异不足：{spread(1):.2f} vs {spread(60):.2f}"
        )

    def test_positional_fields_survive_fusion(self):
        """**阶段 2.7 的前提**：chunk 的定位字段必须穿过融合层

        旧实现用一张白名单（只有 card_id/title/chapter_title）挑字段，
        chunk 的 `char_start/char_end/heading_path` 在这里被丢掉 ——
        引用回跳的链路断在第一层（附录 N.4）。
        """
        chunk = {
            "note_id": "n1", "content": "原文段落", "note_title": "笔记", "block_index": 0,
            "chunk_id": "c1", "index": 3,
            "char_start": 100, "char_end": 200,
            "heading_path": "第一章 > 1.2", "line_start": 5, "line_end": 9,
        }
        fused = RAGService._rrf_fusion([chunk], [], k=1, bm25_weight=0.5)
        assert len(fused) == 1
        got = fused[0]
        for key, want in (
            ("chunk_id", "c1"), ("char_start", 100), ("char_end", 200),
            ("heading_path", "第一章 > 1.2"), ("line_start", 5), ("line_end", 9),
        ):
            assert got.get(key) == want, f"融合后丢失定位字段 {key}: {got}"

    def test_content_is_not_truncated_by_dedupe_key(self):
        """去重键是截断的，但**返回值必须是完整内容**

        这是实测踩过的坑：早期测量脚本把截断到 200 字的去重键当成文档返回，
        评测判据因此大量误判，得出"融合比单通道差 28 个百分点"的假结论
        （附录 P.4）。若不锁住，同样的错误会以"检索质量下降"的形式重现。
        """
        long_content = "甲" * 500
        doc = {"note_id": "n1", "content": long_content, "note_title": None, "block_index": 0}
        fused = RAGService._rrf_fusion([doc], [], k=1, bm25_weight=0.5)
        assert fused[0]["content"] == long_content
        assert len(fused[0]["content"]) == 500

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
class TestChunkCorpus:
    """`_get_user_chunks` / `_search_chunk_vectors`：阶段 2.3 的统一语料

    这两条路径取代了卡片语料：BM25 与向量路现在跑**同一份 chunk 语料**，
    定位字段因此能贯通到引用回跳（2.7）。
    """

    async def test_returns_plain_dicts_with_positional_fields(self, test_db):
        """必须返回普通 dict，且带全定位字段

        两个要求都有来历：
        - 普通 dict：原实现缓存 ORM 实例，与 session 绑定，
          session 关闭后访问未加载属性会抛 `DetachedInstanceError`，
          属于"平时不出现、并发时偶发"的失败
        - 定位字段：引用回跳按 `char_start/char_end` 切片、按 `heading_path`
          显示面包屑。缺了它们，2.7 无处可跳
        """
        uid, note_id = await _make_user_and_note(test_db)
        await _add_chunks(test_db, uid, note_id)

        service = RAGService()
        service._session_factory = test_db
        chunks = await service._get_user_chunks(uid)

        assert len(chunks) == 2
        assert isinstance(chunks[0], dict), f"返回了 {type(chunks[0])}，应为 dict"
        required = {
            "chunk_id", "note_id", "index", "title", "content", "chapter_title",
            "char_start", "char_end", "heading_path", "line_start", "line_end",
        }
        assert required <= set(chunks[0]), (
            f"缺少字段: {required - set(chunks[0])}"
        )
        # 偏移必须自洽：区间长度等于内容长度
        for c in chunks:
            assert c["char_end"] - c["char_start"] == len(c["content"])

    async def test_excludes_chunks_of_trashed_notes(self, test_db):
        """回收站笔记的 chunk 不得进入 QA 语料

        否则用户删掉的资料仍会被问答引用 —— 既违反直觉，
        也可能把用户主动清理的内容重新"答"出来。
        """
        from datetime import datetime

        uid, note_id = await _make_user_and_note(test_db)
        await _add_chunks(test_db, uid, note_id)

        async with test_db() as db:
            note = (await db.execute(_select_note(note_id))).scalar_one()
            note.trashed_at = datetime.utcnow()
            await db.commit()

        service = RAGService()
        service._session_factory = test_db
        chunks = await service._get_user_chunks(uid)

        assert chunks == [], (
            f"回收站笔记的 chunk 仍在语料中: {[c['content'] for c in chunks]}"
        )

    async def test_includes_unembedded_chunks(self, test_db):
        """未嵌入的 chunk **必须**留在 BM25 语料里

        BM25 是纯词法检索，不需要向量。清洗完成后到跑嵌入之间有窗口期，
        若把未嵌入的行排除，新资料在那个窗口里完全查不到；
        保留则只是"少一路召回"（降级而非不可用）。
        """
        uid, note_id = await _make_user_and_note(test_db)
        await _add_chunks(test_db, uid, note_id, has_embedding=False)

        service = RAGService()
        service._session_factory = test_db
        chunks = await service._get_user_chunks(uid)

        assert len(chunks) == 2, "未嵌入的 chunk 被排除在词法语料之外"

    async def test_isolated_per_user(self, test_db):
        """跨用户语料必须隔离（不得把别人的资料喂进问答）"""
        uid_a, note_a = await _make_user_and_note(test_db)
        uid_b, note_b = await _make_user_and_note(test_db)
        await _add_chunks(test_db, uid_a, note_a, content="A 的内容")
        await _add_chunks(test_db, uid_b, note_b, content="B 的内容")

        service = RAGService()
        service._session_factory = test_db
        chunks = await service._get_user_chunks(uid_a)

        assert all("A 的内容" in c["content"] for c in chunks), "语料未按 user_id 隔离"

    async def test_vector_search_excludes_trashed_notes(self, test_db):
        """向量路也必须排除回收站笔记

        向量检索直接查 `chunks` 表（不再经 `get_user_chunks`），
        所以它需要**单独**做回收站过滤 —— 漏掉就会让已删除的资料
        继续出现在回答里。这是一条容易被"另一条路已经处理了"掩盖的路径。
        """
        uid, note_id = await _make_user_and_note(test_db)
        await _add_chunks(test_db, uid, note_id, has_embedding=True, dim=4)

        service = RAGService()
        service._session_factory = test_db

        vec = [1.0, 0.0, 0.0, 0.0]
        hits = await service._search_chunk_vectors(vec, uid, top_k=5)
        assert hits, "未回收时应当检索得到"

        from datetime import datetime
        async with test_db() as db:
            note = (await db.execute(_select_note(note_id))).scalar_one()
            note.trashed_at = datetime.utcnow()
            await db.commit()

        hits_after = await service._search_chunk_vectors(vec, uid, top_k=5)
        assert hits_after == [], (
            f"回收站笔记的 chunk 仍被向量检索到: {[h['chunk_id'] for h in hits_after]}"
        )

    async def test_vector_search_returns_positional_fields(self, test_db):
        """向量结果必须带定位字段 —— 2.7 回跳的数据基础

        旧实现（Chroma 路径）只往上传 `block_index`，
        `start_line/end_line` 在 `embedding_tasks` 里就被丢掉了（附录 N.4）。
        """
        uid, note_id = await _make_user_and_note(test_db)
        await _add_chunks(test_db, uid, note_id, has_embedding=True, dim=4)

        service = RAGService()
        service._session_factory = test_db
        hits = await service._search_chunk_vectors([1.0, 0.0, 0.0, 0.0], uid, top_k=5)

        assert hits
        for key in ("chunk_id", "char_start", "char_end", "heading_path",
                    "line_start", "line_end"):
            assert key in hits[0], f"向量结果缺少定位字段 {key}"


class TestCitationSources:
    """阶段 2.7 后端：引用来源的编号一致性与定位字段

    抽成纯函数才能测 —— 这段逻辑原先内嵌在 `retrieve_context` 里，
    而那条路径要调用 LLM 才能跑完，于是下面两条关键一致性
    在改造前**没有任何测试覆盖**。
    """

    @staticmethod
    def _items():
        """两条来自同一笔记、一条来自另一笔记（按原文位置已排序）"""
        return [
            {"note_id": "n1", "note_title": "笔记甲", "chapter_title": "第一章",
             "content": "第一段内容", "chunk_id": "c1", "index": 0,
             "char_start": 0, "char_end": 10, "heading_path": "第一章",
             "line_start": 0, "line_end": 3},
            {"note_id": "n1", "note_title": "笔记甲", "chapter_title": "第二章",
             "content": "第二段内容", "chunk_id": "c2", "index": 1,
             "char_start": 20, "char_end": 30, "heading_path": "第二章",
             "line_start": 5, "line_end": 9},
            {"note_id": "n2", "note_title": "笔记乙", "chapter_title": None,
             "content": "另一篇内容", "chunk_id": "c3", "index": 0,
             "char_start": 0, "char_end": 8, "heading_path": None,
             "line_start": 0, "line_end": 2},
        ]

    def test_context_numbering_matches_sources_order(self):
        """**关键一致性**：上下文 `[N]` 必须对应 `sources[N-1]`

        提示词要求回答逐条标注 `[编号]`。若上下文按融合排名编号、
        而 sources 按原文位置排序，回答里的 `[1]` 会指向 sources 的另一项 ——
        用户点第一条引用跳到别处。这类错位不报错，只会让人以为系统在胡说。
        """
        from app.services.rag_service import build_context_and_sources

        context, sources = build_context_and_sources(self._items())

        for i, src in enumerate(sources, start=1):
            assert f"[{i}] 来源: {src['note_title']}" in context, (
                f"上下文里缺少与 sources[{i-1}] 对应的 [{i}] 编号"
            )
            # 该编号下的内容必须是这一条的内容，而不是别人的
            block = context.split(f"[{i}] 来源:", 1)[1]
            assert src["relevant_text"][:10] in block, (
                f"[{i}] 指向的内容与 sources[{i-1}] 不一致"
            )

    def test_keeps_multiple_chunks_from_same_note(self):
        """同一笔记的多个 chunk 都要保留

        原实现按 `note_id` 去重，同一篇笔记只留第一个 chunk ——
        用户看到"第 3 段来自某笔记"却只能跳到该笔记的第 1 段。
        """
        from app.services.rag_service import build_context_and_sources

        _, sources = build_context_and_sources(self._items())

        assert len(sources) == 3, f"同一笔记的多个 chunk 被去重了: {sources}"
        assert [s["chunk_id"] for s in sources] == ["c1", "c2", "c3"]

    def test_duplicate_chunk_is_dropped(self):
        """同一个 chunk 出现两次（两路都命中）时只保留一条"""
        from app.services.rag_service import build_context_and_sources

        items = self._items()
        items.append(dict(items[0]))  # 完全重复
        _, sources = build_context_and_sources(items)

        assert len(sources) == 3
        assert [s["chunk_id"] for s in sources] == ["c1", "c2", "c3"]

    def test_positional_fields_reach_sources(self):
        """定位字段必须出现在 sources 里 —— 前端跳转的数据基础"""
        from app.services.rag_service import build_context_and_sources

        _, sources = build_context_and_sources(self._items())

        s = sources[0]
        for key, want in (
            ("chunk_id", "c1"), ("chunk_index", 0),
            ("char_start", 0), ("char_end", 10),
            ("heading_path", "第一章"),
            ("line_start", 0), ("line_end", 3),
        ):
            assert s.get(key) == want, f"sources 缺少/错位定位字段 {key}: {s}"

    def test_heading_path_falls_back_to_chapter_title(self):
        """`heading_path` 缺失时退回 `chapter_title`（两条路径来源不同）"""
        from app.services.rag_service import build_context_and_sources

        items = [{
            "note_id": "n1", "note_title": "甲", "chapter_title": "第三章",
            "content": "内容", "chunk_id": "c1", "index": 0,
            "char_start": 0, "char_end": 2, "heading_path": None,
        }]
        _, sources = build_context_and_sources(items)
        assert sources[0]["heading_path"] == "第三章"

    def test_title_lookup_fills_missing_titles(self):
        """BM25 结果不带 note_title → 用回查到的标题补齐"""
        from app.services.rag_service import build_context_and_sources

        items = [{
            "note_id": "n9", "note_title": None, "chapter_title": None,
            "content": "内容", "chunk_id": "c9", "index": 0,
            "char_start": 0, "char_end": 2,
        }]
        context, sources = build_context_and_sources(items, {"n9": "回查标题"})
        assert sources[0]["note_title"] == "回查标题"
        assert "回查标题" in context

    def test_missing_title_degrades_gracefully(self):
        """标题彻底查不到时用占位文案，不得抛异常或产出 None"""
        from app.services.rag_service import build_context_and_sources

        items = [{
            "note_id": "n9", "note_title": None, "chapter_title": None,
            "content": "内容", "chunk_id": "c9", "index": 0,
            "char_start": 0, "char_end": 2,
        }]
        _, sources = build_context_and_sources(items, {})
        assert sources[0]["note_title"] == "未知笔记"

    def test_items_without_note_id_are_skipped(self):
        """没有 note_id 的结果不能进 sources（无法定位到任何笔记）"""
        from app.services.rag_service import build_context_and_sources

        items = self._items() + [{"note_id": None, "content": "孤儿内容"}]
        _, sources = build_context_and_sources(items)
        assert all(s["note_id"] for s in sources)

    def test_empty_input(self):
        from app.services.rag_service import build_context_and_sources

        context, sources = build_context_and_sources([])
        assert context == ""
        assert sources == []


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


class TestBM25ScoringUnchanged:
    """重构等价性：`BM25Index` 的打分必须与重构前的实现逐条一致

    ## 为什么要有这个测试

    阶段 2.9 把 BM25 从"每次调用重建索引"改成 `BM25Index`（建一次、查多次），
    并把打分函数从 `async def _search_bm25(self, question, user_id, top_k)`
    改成静态纯函数。**重构检索打分的风险在于分数悄悄变了** ——
    排序变了，检索质量就变了，而没有任何测试会发现。

    下面把**重构前的原始算法原样抄写**（`_legacy_bm25`）作为参照，
    在同一批数据上逐条比对分数。参照实现刻意保留"低效"的写法，
    因为它要证明的是"行为未变"，不是"写法好看"。
    """

    #: 与生产同构的测试语料
    CARDS = [
        {"card_id": "c1", "note_id": "n1", "title": "水电站",
         "content": "拉哇水电站装设多台水轮发电机组", "chapter_title": "第一章"},
        {"card_id": "c2", "note_id": "n2", "title": "变电站",
         "content": "500kV 配电装置采用户外敞开式布置", "chapter_title": None},
        {"card_id": "c3", "note_id": "n3", "title": "继电保护",
         "content": "线路保护应双重化配置", "chapter_title": "第三章"},
        {"card_id": "c4", "note_id": "n4", "title": "无关内容",
         "content": "今天天气很好", "chapter_title": None},
    ]

    @staticmethod
    def _legacy_bm25(question: str, cards, top_k: int = 5):
        """重构前的原始实现（逐字抄写，勿"优化"）"""
        import math

        if not cards:
            return []
        query_tokens = RAGService._tokenize(question)
        if not query_tokens:
            return []

        docs = []
        for card in cards:
            doc_text = f"{card['title']} {card['content']}"
            doc_tokens = RAGService._tokenize(doc_text)
            docs.append({"card": card, "tokens": doc_tokens, "len": len(doc_tokens)})
        if not docs:
            return []

        k1, b = 1.5, 0.75
        N = len(docs)
        avgdl = sum(d["len"] for d in docs) / N if N > 0 else 0.0

        df = {}
        for doc in docs:
            for token in set(doc["tokens"]):
                df[token] = df.get(token, 0) + 1
        idf = {t: math.log((N - f + 0.5) / (f + 0.5) + 1) for t, f in df.items()}

        scored = []
        for doc in docs:
            score = 0.0
            token_freq = {}
            for token in doc["tokens"]:
                token_freq[token] = token_freq.get(token, 0) + 1
            for query_token in query_tokens:
                if query_token not in token_freq:
                    continue
                tf = token_freq[query_token]
                idf_val = idf.get(query_token, 0.0)
                numerator = tf * (k1 + 1)
                if avgdl > 0:
                    denominator = tf + k1 * (1 - b + b * doc["len"] / avgdl)
                else:
                    denominator = tf + k1
                if denominator > 0:
                    score += idf_val * numerator / denominator
            if score > 0:
                card = doc["card"]
                scored.append({
                    "note_id": card["note_id"], "note_title": None,
                    "content": card["content"], "similarity": score,
                    "block_index": 0, "card_id": card["card_id"],
                    "title": card["title"], "chapter_title": card["chapter_title"],
                })
        scored.sort(key=lambda x: x["similarity"], reverse=True)
        return scored[:top_k]

    @pytest.mark.parametrize("question", [
        "水电站装设什么",
        "500kV 配电装置",
        "继电保护配置要求",
        "天气",
        "完全无关的查询词",
        "a",
        "",
    ])
    def test_scores_match_legacy_implementation(self, question):
        """逐条比对：新实现与重构前实现的分数、顺序、字段必须完全一致

        走的是**线上同一条路径**（`build_bm25_index().search()`），
        而不是某个只为测试存在的包装函数 —— 否则测的可能不是线上跑的东西。
        """
        legacy = self._legacy_bm25(question, self.CARDS)
        new = RAGService.build_bm25_index(self.CARDS).search(question)

        assert len(new) == len(legacy), f"结果条数不同: {len(new)} vs {len(legacy)}"
        for got, want in zip(new, legacy, strict=True):
            assert got["card_id"] == want["card_id"], "排序或命中集合发生变化"
            assert got["similarity"] == pytest.approx(want["similarity"], rel=1e-12), (
                f"BM25 分数变化: {got['similarity']} vs {want['similarity']}"
            )
            assert got == want, "结果字段发生变化"

    def test_index_reuse_does_not_change_results(self):
        """复用索引与每次重建索引必须给出相同结果

        索引复用的前提是"语料没变"。若复用时残留了上一次查询的状态
        （例如误把 query 词频写进了文档词频），结果就会随调用顺序变化 ——
        这是缓存类重构最典型的一类 bug。
        """
        index = RAGService.build_bm25_index(self.CARDS)

        first = index.search("水电站装设什么")
        # 中间穿插别的查询，再看第一条是否受影响
        index.search("继电保护")
        index.search("500kV 配电装置")
        again = index.search("水电站装设什么")

        assert first == again, "复用索引后同一条查询的结果发生了变化"

    def test_index_signature_detects_corpus_change(self):
        """语料增删必须改变指纹（否则会一直用旧索引）"""
        base = RAGService.build_bm25_index(self.CARDS)
        added = RAGService.build_bm25_index(
            self.CARDS + [{"card_id": "c9", "note_id": "n9", "title": "新卡片",
                           "content": "新增内容", "chapter_title": None}]
        )
        removed = RAGService.build_bm25_index(self.CARDS[:-1])

        assert base.signature != added.signature, "新增卡片未改变指纹"
        assert base.signature != removed.signature, "删除卡片未改变指纹"
        assert base.signature == RAGService.build_bm25_index(self.CARDS).signature

    def test_index_reuse_avoids_rebuild(self):
        """语料未变时不得重建索引（A-5 的核心诉求）

        通过计数分词调用次数验证：第二次取索引不应再分词。
        只断言"结果相同"是不够的 —— 每次重建也得到相同结果，
        那正是重构前的问题（每次提问全量重分词）。
        """
        service = RAGService()
        calls = {"n": 0}

        def counting_tokenize(text):
            calls["n"] += 1
            return RAGService._tokenize(text)

        service._tokenize = counting_tokenize  # type: ignore[method-assign]

        service._get_bm25_index("u1", self.CARDS)
        after_first = calls["n"]
        assert after_first > 0, "首次构建应当分词"

        service._get_bm25_index("u1", self.CARDS)
        assert calls["n"] == after_first, (
            f"语料未变却重新分词了 {calls['n'] - after_first} 次（索引未被复用）"
        )

    def test_index_slot_does_not_grow_with_users(self):
        """索引槽位必须有硬上界（不得重蹈 `_kb_cache` 覆辙）

        换用户时整体替换单个槽位，而不是为每个用户各留一份。
        """
        service = RAGService()
        for uid in ("u1", "u2", "u3", "u4"):
            service._get_bm25_index(uid, self.CARDS)

        # 槽位是单个 tuple，不是字典 —— 结构上就不可能随用户数增长
        assert isinstance(service._bm25_slot, tuple)
        assert len(service._bm25_slot) == 3
        assert service._bm25_slot[0] == "u4", "槽位应只保留最近一个用户"


class TestRetrievalEvalHarness:
    """阶段 2.9 评测脚本自身的守护

    评测脚本如果悄悄坏掉（例如判据失效、返回 0 条），会给出误导性的
    "质量下降"结论，进而导致错误的整改方向。这类"度量工具自身失准"
    比被测代码出错更危险，所以它也要有测试。

    注意：这里**不**把绝对指标写死成断言。指标取决于真实资料内容，
    写死会让测试在资料变化时误报。这里只锁**判据的行为性质**：
    对无关内容不能送分、对包含内容必须给分、对长度不对称必须中立。
    """

    @staticmethod
    def _overlaps(expected, actual):
        from scripts.eval_retrieval import _overlaps

        return _overlaps(expected, actual)

    @staticmethod
    def _containment(expected, actual):
        from scripts.eval_retrieval import _containment

        return _containment(expected, actual)

    def test_unrelated_content_scores_zero(self):
        """无关内容必须得 0 分（否则指标虚高到没有意义）"""
        assert self._containment("水电站装机容量与机组", "今天天气很好适合出门散步") == 0.0
        assert not self._overlaps("水电站装机容量与机组", "今天天气很好适合出门散步")

    def test_containing_content_scores_high(self):
        """检索内容完整包含真值时必须判为命中（两档都是）"""
        truth = "3.1.1 线路断路器：合上、断开。3.1.2 隔离刀闸：合上、拉开。"
        retrieved = "操作术语如下：" + truth + "（以上为全部术语）"
        assert self._containment(truth, retrieved) >= 0.99
        assert self._overlaps(truth, retrieved)

    def test_length_asymmetry_is_neutral(self):
        """**关键性质**：检索单元更大不得受罚

        第一版宽松判据用 Jaccard，实测导致原文 chunk 语料被系统性低估
        （200 字真值 vs 2000 字 chunk → Jaccard ≈ 0.10）。
        包含度必须对这种情况给高分。
        """
        truth = "拉哇水电站装设多台水轮发电机组，总装机容量为 2000MW。"
        big = ("无关的前置内容。" * 200) + truth + ("无关的后置内容。" * 200)
        small = truth

        assert self._containment(truth, big) >= 0.99, (
            "检索单元比真值大得多时被误判为不相关（Jaccard 的老问题）"
        )
        assert self._containment(truth, small) >= 0.99
        # 大到 100 倍仍应命中：长度不应影响判定
        assert self._overlaps(truth, big)

    def test_whitespace_and_newlines_are_normalized(self):
        """换行差异不得造成未命中（实测踩过：编号列表被换行拆开）"""
        truth = "3.1.1 断路器：合上、断开。\n3.1.2 隔离刀闸：合上、拉开。"
        retrieved = "3.1.1 断路器：合上、断开。 3.1.2 隔离刀闸：合上、拉开。"
        assert self._containment(truth, retrieved) >= 0.99

    def test_partial_overlap_below_threshold(self):
        """只覆盖真值一小部分时不得判为命中（否则指标失去区分度）

        `"拉哇水电站"` 是被检索内容，`MIN_SHORT_SIDE` 之前它会因"互相包含"
        回退分支命中任何含这个词的长真值 —— 只取回一个实体名不算找到答案。
        """
        truth = "拉哇水电站装设多台水轮发电机组，总装机容量为 2000MW，年发电量约 80 亿千瓦时。"
        tiny = "拉哇水电站"
        assert self._containment(truth, tiny) < 0.5
        assert not self._overlaps(truth, tiny), "过短的检索内容不应命中长真值"
        # 但逐字相等仍应命中（真值本身很短时）
        assert self._overlaps(tiny, tiny)

    def test_cli_produces_report_on_real_db(self):
        """CLI 端到端可跑（在真实库上只读跑 5 条）

        防止"脚本语法正确但跑不起来"—— 评测脚本是离线工具，
        不会在应用启动路径上被 import，因此语法/依赖错误不会被其他测试发现。
        """
        import subprocess
        import sys as _sys

        backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        proc = subprocess.run(
            [_sys.executable, os.path.join(backend, "scripts", "eval_retrieval.py"),
             "--limit", "5", "--corpus", "cards", "--show-missed", "0"],
            cwd=backend, capture_output=True, text=True, timeout=600,
            # 显式 utf-8：脚本输出含中文，Windows 默认用 GBK 解码子进程输出，
            # 会在读取线程里抛 UnicodeDecodeError（而不是让断言失败），
            # 报错形态与真实问题完全无关，极难排查。
            encoding="utf-8", errors="replace",
        )
        assert proc.returncode == 0, f"评测脚本退出码 {proc.returncode}: {proc.stderr[-800:]}"
        assert "Recall@5" in proc.stdout, f"输出缺少指标:\n{proc.stdout[-800:]}"
        assert "评测问题数    : 5" in proc.stdout, "未按 --limit 限制评测条数"


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


async def _add_chunks(
    session_factory,
    user_id: str,
    note_id: str,
    *,
    content: str = "拉哇水电站装设多台水轮发电机组。",
    has_embedding: bool = False,
    dim: int = 4,
) -> None:
    """写入两个 chunk（可选：带向量）

    偏移刻意做成自洽的（`char_end - char_start == len(content)`），
    因为测试要断言这一点；真实的偏移由 `segment_with_offsets` 保证。
    """
    from sqlalchemy import insert

    from app.models.chunk import pack_vector

    async with session_factory() as db:
        for i in range(2):
            vec = [1.0] + [0.0] * (dim - 1) if has_embedding else None
            await db.execute(insert(Chunk).values(
                user_id=user_id,
                note_id=note_id,
                index=i,
                content=f"{content}#{i}",
                char_start=i * 100,
                char_end=i * 100 + len(f"{content}#{i}"),
                heading_path=f"第一章 > 1.{i}",
                line_start=i * 5,
                line_end=i * 5 + 3,
                char_count=len(f"{content}#{i}"),
                source_md_path=f"{note_id}/clean.md",
                content_hash="testhash",
                has_embedding=has_embedding,
                embedding=pack_vector(vec) if vec else None,
                embedding_model="test-model" if vec else None,
                embedding_dim=dim if vec else None,
            ))
        await db.commit()
