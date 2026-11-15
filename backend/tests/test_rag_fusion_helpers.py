"""检索融合层与状态判定（审计 M5：本轮新逻辑此前零测试）。

覆盖三件容易悄悄坏掉的事：

1. `_ensure_lexical_hits`：词法保底名额是否真的补入、会不会重复、分数是否冒充高分；
2. `retrieval_status == vector_dim_mismatch`：整批维度不符时必须与 `hybrid` 区分；
3. `_chunk_identity`：兜底比较两侧截断一致（否则同一条 chunk 被插两次，审计 m1）。
"""

from __future__ import annotations

import pytest

from app.services.rag_service import RAGService


def _item(chunk_id=None, note_id="n1", content="正文", similarity=0.5):
    return {
        "chunk_id": chunk_id,
        "note_id": note_id,
        "content": content,
        "similarity": similarity,
    }


class TestEnsureLexicalHits:
    def test_inserts_missing_lexical_items_without_fake_high_scores(self):
        svc = RAGService()
        fused = [_item("c1", similarity=0.9), _item("c2", similarity=0.8)]
        lexical = [_item("c3", content="只被词法命中的块", similarity=0.0)]

        out = svc._ensure_lexical_hits(fused, lexical, top_k=3, quota=2)

        ids = [it.get("chunk_id") for it in out]
        assert "c3" in ids, "词法强命中没被补入"
        # 不许冒充高分：补入项的分数不得高于原本最后一名
        inserted = next(it for it in out if it.get("chunk_id") == "c3")
        assert inserted["similarity"] <= 0.8
        assert inserted.get("lexical_guaranteed") is True

    def test_does_not_insert_duplicates(self):
        svc = RAGService()
        # 同一条 chunk 已在前 2 名里
        fused = [_item("c1"), _item("c2")]
        lexical = [_item("c1"), _item("c2")]

        out = svc._ensure_lexical_hits(fused, lexical, top_k=3, quota=2)
        assert [it["chunk_id"] for it in out] == ["c1", "c2"]

    def test_falls_back_to_content_truncated_identity(self):
        """没有 chunk_id 时用（note_id, 前 80 字）判同一条（审计 m1）"""
        svc = RAGService()
        long_text = "同样的正文" * 40  # >200 字
        fused = [dict(_item(None, content=long_text))]
        lexical = [dict(_item(None, content=long_text))]

        out = svc._ensure_lexical_hits(fused, lexical, top_k=3, quota=2)
        assert len(out) == 1, "同一条 chunk 被插了两次（两处截断长度不一致）"

    def test_quota_zero_is_noop(self):
        svc = RAGService()
        fused = [_item("c1")]
        out = svc._ensure_lexical_hits(fused, [_item("c2")], top_k=2, quota=0)
        assert [it["chunk_id"] for it in out] == ["c1"]


@pytest.mark.asyncio
class TestRetrievalStatus:
    async def test_all_vectors_skipped_reports_dim_mismatch(self, monkeypatch):
        """整批维度不符 → `vector_dim_mismatch`，而不是 `hybrid`（审计 blocker #1）"""
        svc = RAGService()

        async def _encode(_text):
            return [1.0, 0.0, 0.0]

        async def _vectors(_emb, _uid, top_k=5, stats=None):
            if stats is not None:
                stats.update({"scanned": 890, "skipped_dim": 890, "matched_dim": 0, "all_skipped": 1})
            return []

        async def _lexical(_uid, _q, _chunks, _top_k, exclude_note_ids=None):
            return [_item("c1", content="词法命中的内容")]

        async def _chunks(_uid):
            return []

        async def _trashed(_uid):
            return set()

        async def _titles(_ids):
            return {}

        monkeypatch.setattr(svc, "_encode_via_celery", _encode)
        monkeypatch.setattr(svc, "_search_chunk_vectors", _vectors)
        monkeypatch.setattr(svc, "_lexical_search", _lexical)
        monkeypatch.setattr(svc, "_get_user_chunks", _chunks)
        monkeypatch.setattr(svc, "_trashed_note_ids", _trashed)
        monkeypatch.setattr(svc, "_lookup_note_titles", lambda _ids: _titles(_ids), raising=False)

        result = await svc.retrieve_context(question="水导轴承有几块", user_id="u1")
        assert result["retrieval_status"] == "vector_dim_mismatch"
        # 降级但仍有词法结果：上下文不该空
        assert "水导轴承" in result["context"] or result["sources"]

    async def test_empty_vector_without_all_skipped_is_hybrid(self, monkeypatch):
        """只是没命中（没有整批跳过）→ 仍是 `hybrid`"""
        svc = RAGService()

        async def _encode(_text):
            return [1.0, 0.0, 0.0]

        async def _vectors(_emb, _uid, top_k=5, stats=None):
            if stats is not None:
                stats.update({"scanned": 890, "skipped_dim": 0, "matched_dim": 890})
            return []

        async def _lexical(_uid, _q, _chunks, _top_k, exclude_note_ids=None):
            return []

        async def _chunks(_uid):
            return []

        async def _trashed(_uid):
            return set()

        monkeypatch.setattr(svc, "_encode_via_celery", _encode)
        monkeypatch.setattr(svc, "_search_chunk_vectors", _vectors)
        monkeypatch.setattr(svc, "_lexical_search", _lexical)
        monkeypatch.setattr(svc, "_get_user_chunks", _chunks)
        monkeypatch.setattr(svc, "_trashed_note_ids", _trashed)

        result = await svc.retrieve_context(question="随便问问", user_id="u1")
        assert result["retrieval_status"] == "hybrid"
