"""A2 用例（qa-gap-triage 报告验收要求：三种状态各一条）。

上下文为空时，文案必须按 `retrieval_status` 分因：
- `vector_dim_mismatch` / `bm25_only` 是**系统侧故障**，说成"资料里没有"会误导用户；
- 其它（真的没命中）保持原文案。
"""

from __future__ import annotations

import pytest

from app.services.rag_service import RAGService


def _empty_retrieval(status: str) -> dict:
    return {
        "context": "",
        "sources": [],
        "provider": "deepseek",
        "retrieval_status": status,
        "corpus_empty": False,
    }


@pytest.mark.asyncio
class TestRefusalReasons:
    async def _answer(self, monkeypatch, status: str) -> str:
        svc = RAGService()

        async def _retrieve(_question, _user_id, **_kwargs):
            return _empty_retrieval(status)

        monkeypatch.setattr(svc, "retrieve_context", _retrieve)
        result = await svc.answer_question("随便问问", "u1")
        return result["answer"]

    async def test_dim_mismatch_says_index_unavailable(self, monkeypatch):
        answer = await self._answer(monkeypatch, "vector_dim_mismatch")
        assert "向量检索当前不可用" in answer, answer
        assert "索引与当前模型不一致" in answer, answer
        # 关键：不能把系统故障说成"资料里没有"
        assert "在你的资料中没有找到" not in answer, answer

    async def test_bm25_only_says_encoding_failed(self, monkeypatch):
        answer = await self._answer(monkeypatch, "bm25_only")
        assert "向量检索当前不可用" in answer, answer
        assert "问题编码失败" in answer, answer
        assert "在你的资料中没有找到" not in answer, answer

    async def test_plain_miss_keeps_original_wording(self, monkeypatch):
        answer = await self._answer(monkeypatch, "hybrid")
        assert "在你的资料中没有找到" in answer, answer
        assert "向量检索当前不可用" not in answer, answer
