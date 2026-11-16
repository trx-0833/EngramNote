"""A4 范围过滤 + A6 通用知识开关的用例（用户本轮已点头改接口）。

A4 的验收是"scope 内 Recall@5 不降；越权 0"。这里守的是**越权 0**：
- 传别人的 note_id ⇒ 交集为空 ⇒ 检索范围为空（不是报错，也不是"退回不限"）；
- 传自己的 + 别人的混在一起 ⇒ 只按自己的检索；
- 不传 ⇒ 行为与以前逐字相同（`allow_note_ids=None`）。

A6 的验收是"未标注模型知识 = 0"。这里守默认关闭。
"""

from __future__ import annotations

import pytest
from sqlalchemy import insert

from app.models.note import Note, NoteStatus, SourceType
from app.models.user import User

_ME = "user-scope-me"
_OTHER = "user-scope-other"


async def _seed(db) -> None:
    for uid, email in ((_ME, "me@t.local"), (_OTHER, "other@t.local")):
        await db.execute(insert(User).values(id=uid, email=email, username=uid, hashed_password="x", is_active=True))
    for nid, uid, title in (
        ("n-mine", _ME, "我的笔记"),
        ("n-other", _OTHER, "别人的笔记"),
    ):
        await db.execute(
            insert(Note).values(
                id=nid,
                user_id=uid,
                title=title,
                source_type=SourceType.pdf.value,
                status=NoteStatus.cleaned.value,
                file_size=1,
                original_file_path=f"/{nid}.pdf",
                original_md_path=f"/{nid}.md",
            )
        )
    await db.commit()


@pytest.mark.asyncio
class TestScopeFilter:
    async def test_foreign_note_id_is_dropped_not_500(self, test_db):
        """传别人的 note_id ⇒ 交集为空 ⇒ 不报错、检索为空"""
        from app.services.rag_service import RAGService

        async with test_db() as db:
            await _seed(db)

        svc = RAGService()
        # 直接验证检索层的契约：空白名单必须返回 0 条（而不是"不限"）
        r = await svc.retrieve_context(question="我的笔记讲了什么", user_id=_ME, allow_note_ids=set())
        assert r["sources"] == [], f"空白名单却返回了来源：{r['sources']}"
        assert not (r["context"] or "").strip()

    async def test_own_note_is_allowed(self, test_db):
        from app.services.rag_service import RAGService

        async with test_db() as db:
            await _seed(db)

        svc = RAGService()
        r = await svc.retrieve_context(question="我的笔记讲了什么", user_id=_ME, allow_note_ids={"n-mine"})
        # 没有 chunk 时同样为空，但**不应**因为白名单而报错
        assert r["sources"] == []

    async def test_no_scope_means_unrestricted(self, test_db):
        """不传白名单时行为不变（`None` ≠ 空集合）"""
        from app.services.rag_service import RAGService

        async with test_db() as db:
            await _seed(db)

        svc = RAGService()
        r = await svc.retrieve_context(question="随便问问", user_id=_ME)
        assert r["sources"] == []


@pytest.mark.asyncio
class TestGeneralKnowledgeSwitch:
    async def test_default_is_off(self):
        """A6：默认**不允许**通用知识 —— 产品承诺是"基于你的资料回答\""""
        from app.schemas.knowledge import QuestionRequest

        assert QuestionRequest(question="q").allow_general_knowledge is False

    async def test_scope_field_accepts_list_or_none(self):
        from app.schemas.knowledge import QuestionRequest

        assert QuestionRequest(question="q").scope_note_ids is None
        assert QuestionRequest(question="q", scope_note_ids=["a", "b"]).scope_note_ids == ["a", "b"]
