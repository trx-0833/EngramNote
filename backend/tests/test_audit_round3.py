"""第三轮审计要求的回归用例（A5 / A6）。

A2、A8 是前端行为，用例在 `frontend/src/pages/*.test.tsx`。
"""

from __future__ import annotations

import pytest

from app.services.rag_service import RAGService


class TestAskRejectsRetryOf:
    def test_non_stream_endpoint_rejects_retry_of(self):
        """A5：`retry_of` 只属流式端点，`/ask` 收到必须 422（不能静默丢弃）"""
        from pydantic import ValidationError

        from app.schemas.knowledge import QuestionRequest, StreamQuestionRequest

        # 基类不再有该字段，且 `extra='forbid'` ⇒ 构造即报错
        with pytest.raises(ValidationError):
            QuestionRequest(question="q", retry_of="turn-1")

        # 子类正常接受
        req = StreamQuestionRequest(question="q", retry_of="turn-1")
        assert req.retry_of == "turn-1"


@pytest.mark.asyncio
class TestTrashedSetReuse:
    async def test_vector_search_uses_passed_in_set(self, test_db, monkeypatch):
        """A6：传了 `exclude_note_ids` 就不该再查一次回收站（否则一次提问查两遍）"""
        svc = RAGService()
        svc._session_factory = test_db
        calls = {"n": 0}

        async def _counting(_uid):
            calls["n"] += 1
            return {"should-not-be-used"}

        monkeypatch.setattr(svc, "_trashed_note_ids", _counting)

        async def _fake_search(_session, _emb, _uid, *, top_k=5, exclude_note_ids=None, stats=None):
            return []

        monkeypatch.setattr("app.services.chunk_search_service.search_chunks", _fake_search)

        await svc._search_chunk_vectors([1.0, 0.0], "u1", exclude_note_ids={"note-a"})
        assert calls["n"] == 0, "传了集合还在查回收站 —— 这就是'rulesround2 m7′'那个两遍"

        await svc._search_chunk_vectors([1.0, 0.0], "u1")
        assert calls["n"] == 1, "没传集合时应当自己查一次"
