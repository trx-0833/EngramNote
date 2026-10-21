# -*- coding: utf-8 -*-
"""
笔记详情里的「还没建索引」提示（B2 的"可见"那半）
==================================================

## 这一组盯的是什么

`docs/overhaul-plan.md` 附录 BN.3 把「新导入的笔记在人工补嵌入之前只能用 BM25
检索」列为**仅有的两条「确实影响使用」**之一（丙-2），并指出缺的那一环是
**用户看不出来**：

- `/ready` 的 `index.pending_embeddings` 是**运维探针**，用户不会去读；
- 问答时的 SSE `meta.retrieval_status` **要先提一个问题**才会出现。

也就是说"不提问就不知道"。本轮给 `NoteDetailResponse` 加了
`pending_embedding_chunks`，让**打开笔记详情就能看到**。

## 四条判据（各一条用例）

| 场景 | 期望 | 为什么 |
|---|---|---|
| 有未嵌入的 chunk | 报出条数 | 这就是要暴露的状态 |
| 全部已嵌入 | `0`（且前端**不显示**） | "0"是常态，常驻提示会变噪音 |
| 笔记**还没清洗** | `None`（而不是 0） | 还没清洗 = "没有内容可索引"，与"索引建好了"是两件事 |
| 查询失败 | `None`（而不是 0） | "没测到"不能长得像"没有问题"（与 `/ready` 同一条原则） |

## 为什么不用 `app.dependency_overrides`

与 `test_note_export.py` 同一条理由（那里第一节就写了）：本仓库既有测试
**没有一处**用它，`get_note_detail` 也不是 FastAPI 依赖（是普通函数调用）。
`monkeypatch` 是 pytest 自带的，且这里的目标本来就是模块级名字。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timezone

import pytest
from fastapi.testclient import TestClient

from app.models.note import Note, NoteStatus, SourceType
from app.models.user import User
from app.services.auth_service import create_access_token

UTC = timezone.utc


def _client() -> TestClient:
    from app.main import app

    return TestClient(app, client=("198.51.100.32", 9322))


def _auth(user_id: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


@pytest.fixture
def patched_content(monkeypatch):
    """把两个正文读取函数换成离线桩（patch 目标是 `list_detail` 里的名字）

    `list_detail.py` 是 `from ...services.note_service import ...` 的**直接名字
    导入**，所以 patch `note_service` 里那个名字不会生效。
    ⚠️ 桩必须是 async（端点是 `await` 调的）。
    """
    from app.api.notes import list_detail

    async def _clean(note):
        return "# 清洗后正文\n" if note.clean_md_path else None

    async def _original(note):
        return "# 原始正文\n"

    monkeypatch.setattr(list_detail, "get_clean_markdown_content", _clean)
    monkeypatch.setattr(list_detail, "get_note_markdown_content", _original)


def _seed(
    session_factory,
    *,
    status: NoteStatus = NoteStatus.cleaned,
    embedded: int = 0,
    pending: int = 0,
    clean_path: str | None = "/u/n.clean.md",
) -> tuple[str, str]:
    """建用户 + 笔记 + 若干 chunk，返回 (user_id, note_id)

    ⚠️ **必须在 `TestClient(app)` 之后调用**（理由见 `test_note_export.py`）：
    `test_db` 会重建数据库单例，先建库再建 client 会让 client 拿到被丢弃的引擎。
    """
    from app.models.chunk import Chunk

    uid = str(uuid.uuid4())
    nid = str(uuid.uuid4())

    async def _go():
        async with session_factory() as db:
            db.add(
                User(
                    id=uid,
                    email=f"{uid[:8]}@test.local",
                    username=f"u{uid[:8]}",
                    hashed_password="x",
                    is_active=True,
                )
            )
            await db.commit()
        async with session_factory() as db:
            db.add(
                Note(
                    id=nid,
                    user_id=uid,
                    title="带索引缺口的笔记",
                    source_type=SourceType.pdf,
                    status=status,
                    original_md_path="/u/n.md",
                    clean_md_path=clean_path,
                )
            )
            await db.flush()
            for i in range(embedded + pending):
                db.add(
                    Chunk(
                        user_id=uid,
                        note_id=nid,
                        index=i,
                        content=f"第 {i} 段",
                        char_start=i,
                        char_end=i + 1,
                        line_start=i,
                        line_end=i,
                        char_count=1,
                        has_embedding=i < embedded,
                    )
                )
            await db.commit()

    asyncio.run(_go())
    return uid, nid


class TestPendingEmbeddingChunks:
    """端点把"还没建索引"如实报出来（`NoteDetailResponse.pending_embedding_chunks`）"""

    def test_counts_only_unembedded_chunks(self, test_db, patched_content):
        """★ 数的分母是**待补向量**的行，不是 chunk 总数

        这是这个字段的全部用处：3 条已嵌入 + 2 条未嵌入必须报 2 ——
        报 5 会让"索引已建好"的笔记也显示提示。
        """
        client = _client()
        uid, nid = _seed(test_db, embedded=3, pending=2)

        resp = client.get(f"/api/notes/{nid}", headers=_auth(uid))

        assert resp.status_code == 200, resp.text
        assert resp.json()["pending_embedding_chunks"] == 2, resp.json().get(
            "pending_embedding_chunks"
        )

    def test_fully_indexed_note_reports_zero(self, test_db, patched_content):
        """全部已嵌入 ⇒ 0（前端据此**不显示**提示 —— 0 是常态，不是问题）"""
        client = _client()
        uid, nid = _seed(test_db, embedded=4, pending=0)

        resp = client.get(f"/api/notes/{nid}", headers=_auth(uid))

        assert resp.json()["pending_embedding_chunks"] == 0

    def test_no_chunks_at_all_reports_zero_not_none(self, test_db, patched_content):
        """已清洗但一条 chunk 都没有 ⇒ 0（真的没有待补内容）"""
        client = _client()
        uid, nid = _seed(test_db, embedded=0, pending=0)

        resp = client.get(f"/api/notes/{nid}", headers=_auth(uid))

        assert resp.json()["pending_embedding_chunks"] == 0

    @pytest.mark.parametrize(
        "status",
        [NoteStatus.uploading, NoteStatus.converting, NoteStatus.cleaning, NoteStatus.converted],
    )
    def test_uncleaned_note_reports_none_not_zero(self, test_db, patched_content, status):
        """★★ 还没清洗的笔记报 `None`，**不是 0**

        为什么这条最重要：还没清洗的笔记**根本没有 chunk**，报 0 会被读成
        "索引没问题" —— 而事实是"还没有内容可索引"，用户看到 0 会以为可以问了。
        "没测到"与"没有问题"必须能分开（与 `/ready` 的 `pending_embeddings`
        同一条原则：它用 `None` 而不是 0 也是为了这个）。
        """
        client = _client()
        uid, nid = _seed(test_db, status=status, embedded=0, pending=0)

        resp = client.get(f"/api/notes/{nid}", headers=_auth(uid))

        assert resp.status_code == 200, resp.text
        assert resp.json()["pending_embedding_chunks"] is None, (
            f"status={status} 时报了 {resp.json()['pending_embedding_chunks']!r} —— "
            f"未清洗的笔记必须是 None（'没测到'）"
        )

    def test_query_failure_degrades_to_none_and_does_not_break_the_page(
        self, test_db, patched_content, monkeypatch
    ):
        """★★ 统计查询失败 ⇒ `None`，且详情页**照常 200**

        这个字段是笔记详情的**附加提示**。为了它让整页 500 是本末倒置 ——
        而静默报 0 更糟（假的好消息比没消息危险）。
        """
        from app.api.notes import list_detail

        async def _boom(db, note_id):
            raise RuntimeError("数据库忙")

        # ⚠️ patch 目标必须是 **`list_detail` 里那个名字**，不是
        # `_common._count_pending_embedding_chunks` —— `list_detail.py` 是
        # `from ._common import _count_pending_embedding_chunks` 的**直接名字
        # 导入**，它在 import 时就把函数对象绑进了自己的命名空间。
        # 改 `_common` 里那个名字不会影响已经绑好的引用（本文件开头的说明
        # 与 `test_note_export.py` 记的是同一条坑）。
        monkeypatch.setattr(list_detail, "_count_pending_embedding_chunks", _boom)

        client = _client()
        uid, nid = _seed(test_db, embedded=0, pending=3)

        resp = client.get(f"/api/notes/{nid}", headers=_auth(uid))

        assert resp.status_code == 200, "统计失败把整页带崩了"
        assert resp.json()["pending_embedding_chunks"] is None, (
            "统计失败时报了数字 —— 假的好消息比没消息危险"
        )
        # 详情页的其它字段照旧
        assert resp.json()["clean_md_content"] == "# 清洗后正文\n"


class TestFrontendConsumesTheField:
    """把"这个字段真的被前端读了"钉住（防"后端加了没人用"）

    ⚠️ 这是一条**源码级**断言，不是运行时断言 —— 前端的渲染判据由
    `NoteDetailHeader` 的用例守着；这里只防最粗的漏接：
    字段加了、前端一行都没读（本仓库记过至少三处"写好了没人调用"）。
    """

    def test_note_detail_header_reads_pending_embedding_chunks(self):
        import pathlib

        repo = pathlib.Path(__file__).resolve().parents[2]
        src = (repo / "frontend" / "src" / "pages" / "notedetail" / "NoteDetailHeader.tsx")
        text = src.read_text(encoding="utf-8")
        assert "pending_embedding_chunks" in text, (
            "NoteDetailHeader.tsx 没有读 pending_embedding_chunks —— "
            "后端的可见性字段没有出口，用户还是看不到"
        )
        assert "noteDetail.indexPending" in text, "读了字段却没有可见文案"

    def test_i18n_keys_exist_in_both_locales(self):
        import pathlib

        repo = pathlib.Path(__file__).resolve().parents[2]
        for name in ("messages.zh-CN.ts", "messages.en.ts"):
            src = repo / "frontend" / "src" / "i18n" / name
            assert "indexPending" in src.read_text(encoding="utf-8"), (
                f"{name} 缺 indexPending —— 另一种语言下会显示成 key"
            )
