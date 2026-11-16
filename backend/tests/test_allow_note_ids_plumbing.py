"""A4 参数传递的**契约测试**（round7 教训：只测函数不测接线，会被"参数名写错"骗过）。

这条 bug 的真实代价：`allow_note_ids` 传给了一个不接受它的函数 ⇒ TypeError 被 except
吞掉 ⇒ 词法通道静默返回空 ⇒ 检索恒为 0 条，而所有单元测试照样绿。

所以这里用 `inspect.signature` 把**整条链路**的参数名钉住：任何一环漏了，
新加 `allow_note_ids` 的地方在这里就会红。
"""

from __future__ import annotations

import inspect

from app.services.chunk_search_service import search_chunks
from app.services.fts_search_service import search_chunks_fts


class TestAllowNoteIdsPlumbing:
    def test_chunk_search_accepts_allow_note_ids(self):
        assert "allow_note_ids" in inspect.signature(search_chunks).parameters

    def test_fts_search_accepts_allow_note_ids(self):
        """这条就是 round7 漏掉的：传了但函数不收 ⇒ 词法整条静默失效"""
        assert "allow_note_ids" in inspect.signature(search_chunks_fts).parameters

    def test_rag_service_lexical_accepts_allow_note_ids(self):
        from app.services.rag_service import RAGService

        assert "allow_note_ids" in inspect.signature(RAGService._lexical_search).parameters

    def test_rag_service_vectors_accepts_allow_note_ids(self):
        from app.services.rag_service import RAGService

        assert "allow_note_ids" in inspect.signature(RAGService._search_chunk_vectors).parameters

    def test_retrieve_context_accepts_allow_note_ids(self):
        from app.services.rag_service import RAGService

        assert "allow_note_ids" in inspect.signature(RAGService.retrieve_context).parameters
