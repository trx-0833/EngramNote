# -*- coding: utf-8 -*-
"""
批量导出为 ZIP（B4）
======================

## 为什么这条才是"防锁定"的真正价值

`docs/open-source-readiness.md` 写着：单篇导出已落地，但**批量导出（整库搬走）
与导入都还没有**，而「整库可搬走」才是这条真正的价值。

## 这一组盯的四件事（都是"错了不会报错"的类型）

| 判据 | 错了会怎样 |
|---|---|
| **路由顺序** | `GET /api/notes/export` 若注册在 `/{note_id}` **之后**，会被当成 `note_id="export"` 的详情请求 ⇒ 404「笔记不存在」，而 curl 看到的就是 404，很容易误判成"没实现" |
| **文件名去重** | 真库有 72 种重复标题（`标准适用范围` 出现 **9 次**）。`zipfile` 撞名时**不报错、静默覆盖** ⇒ 用户导出 9 篇、解压后只剩 1 篇 |
| **跳过 vs 失败** | 批量导出 200 篇时因为 3 篇没清洗就整包失败，用户只能一个个试错；但**静默**跳过更糟 —— 所以跳过数必须出现在 index.md 与响应头里 |
| **与单篇逐字节一致** | 同一个函数对（`build_front_matter` + `build_export_text`）。两边不一致时用户会得到两种格式的"同一篇笔记" |
"""

from __future__ import annotations

import asyncio
import io
import uuid
import zipfile
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

UTC = timezone.utc


def _client() -> TestClient:
    from app.main import app

    return TestClient(app, client=("198.51.100.61", 9611))


def _auth(user_id: str) -> dict:
    from app.services.auth_service import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


async def _seed_notes(test_db, specs):
    """建用户 + 若干笔记

    specs: [(id, title, clean_path_or_None, original_path_or_None)]
    """
    from app.models.note import Note, NoteStatus, SourceType
    from app.models.user import User

    async with test_db() as session:
        session.add(User(id="u1", email="a@x.c", username="a", hashed_password="x"))
        await session.flush()
        for nid, title, clean_path, original_path in specs:
            session.add(Note(
                id=nid, user_id="u1", title=title, source_type=SourceType.pdf,
                status=NoteStatus.cleaned, file_size=1,
                original_file_path=f"/u1/{nid}.pdf",
                original_md_path=original_path,
                clean_md_path=clean_path,
                page_count=10,
            ))
        await session.commit()


@pytest.fixture
def stub_content(monkeypatch):
    """把两个正文读取函数换成离线桩（patch 目标是 `list_detail` 里的名字）

    ⚠️ 桩必须 async（端点是 `await` 调的）。桩按路径返回正文，
    路径为 None 时返回 None —— 这样才能构造"该版本没有正文"的笔记。
    """
    from app.api.notes import list_detail

    READABLE = {
        "/u1/a.clean.md": "# A 的清洗正文\n\n甲甲甲。\n",
        "/u1/a.md": "# A 的原始正文\n\n甲甲甲（原文）。\n",
        "/u1/b.clean.md": "# B 的清洗正文\n\n乙乙乙。\n",
        "/u1/c.clean.md": "# C 的清洗正文\n\n丙丙丙。\n",
    }

    async def _clean(note):
        return READABLE.get(note.clean_md_path or "")

    async def _original(note):
        return READABLE.get(note.original_md_path or "")

    monkeypatch.setattr(list_detail, "get_clean_markdown_content", _clean)
    monkeypatch.setattr(list_detail, "get_note_markdown_content", _original)


def _zip_of(resp) -> zipfile.ZipFile:
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    return zipfile.ZipFile(io.BytesIO(resp.content))


class TestRouteOrder:
    """★ `/api/notes/export` 不能被 `/{note_id}` 抢走"""

    def test_export_path_is_not_parsed_as_a_note_id(self, test_db, stub_content):
        """★ 直接请求 `/api/notes/export` 必须拿到 ZIP，而不是 404「笔记不存在」

        这是**路由注册顺序**的判据。FastAPI 按注册顺序匹配：若批量导出的
        声明在 `/{note_id}` 之后，这个 URL 会被当成 `note_id="export"` 的详情
        请求，返回 404。而 404 的文案是"笔记不存在" —— 看起来完全不像路由问题。
        """
        asyncio.run(_seed_notes(test_db, [
            ("n-a", "A 笔记", "/u1/a.clean.md", "/u1/a.md"),
        ]))

        resp = _client().get("/api/notes/export", headers=_auth("u1"))

        assert resp.status_code == 200, (
            f"批量导出端点被 /{{note_id}} 抢走了：{resp.status_code} {resp.text[:200]}"
        )
        assert resp.headers["content-type"] == "application/zip"

    def test_single_note_export_still_works(self, test_db, stub_content):
        """反向：单篇导出没被新路由影响"""
        asyncio.run(_seed_notes(test_db, [
            ("n-a", "A 笔记", "/u1/a.clean.md", "/u1/a.md"),
        ]))

        resp = _client().get("/api/notes/n-a/export", headers=_auth("u1"))

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/markdown")


class TestZipContent:
    def test_zip_contains_index_and_one_file_per_note(self, test_db, stub_content):
        """★ 验收判据：ZIP 里有 index.md + 每篇一个 .md"""
        asyncio.run(_seed_notes(test_db, [
            ("n-a", "A 笔记", "/u1/a.clean.md", "/u1/a.md"),
            ("n-b", "B 笔记", "/u1/b.clean.md", "/u1/b.md"),
        ]))

        zf = _zip_of(_client().get("/api/notes/export", headers=_auth("u1")))
        names = zf.namelist()

        assert "index.md" in names, names
        assert "A 笔记.md" in names, names
        assert "B 笔记.md" in names, names
        assert len(names) == 3

    def test_files_are_byte_identical_to_single_note_export(self, test_db, stub_content):
        """★ 同一篇笔记：ZIP 里的文件与单篇导出的正文**逐字相同**（front-matter 除外）

        front-matter 里的 `exported_at` 每次不同，所以逐字比的是**正文部分**。
        两边不一致时用户会得到两种格式的"同一篇笔记"，而那是最容易让人
        怀疑"导出的到底是哪一份"的形态。
        """
        asyncio.run(_seed_notes(test_db, [
            ("n-a", "A 笔记", "/u1/a.clean.md", "/u1/a.md"),
        ]))

        client = _client()
        single = client.get("/api/notes/n-a/export", headers=_auth("u1")).text
        zf = _zip_of(client.get("/api/notes/export", headers=_auth("u1")))
        batch = zf.read("A 笔记.md").decode("utf-8")

        body_single = single.split("---\n", 2)[-1]
        body_batch = batch.split("---\n", 2)[-1]
        assert body_batch == body_single, "同一篇笔记在两条路径下的正文不一致"

    def test_index_lists_the_files_and_the_variant(self, test_db, stub_content):
        asyncio.run(_seed_notes(test_db, [
            ("n-a", "A 笔记", "/u1/a.clean.md", "/u1/a.md"),
        ]))

        zf = _zip_of(_client().get("/api/notes/export", headers=_auth("u1")))
        index = zf.read("index.md").decode("utf-8")

        assert "A 笔记" in index
        # 目录页要写清是哪一版，否则用户分不出手上这份是清洗的还是原始的
        assert "clean" in index

    def test_variant_original_is_honoured(self, test_db, stub_content):
        asyncio.run(_seed_notes(test_db, [
            ("n-a", "A 笔记", "/u1/a.clean.md", "/u1/a.md"),
        ]))

        zf = _zip_of(
            _client().get("/api/notes/export?variant=original", headers=_auth("u1"))
        )
        body = zf.read("A 笔记.md").decode("utf-8")

        assert "原始正文" in body
        assert "清洗正文" not in body


class TestFilenameDedup:
    """★ 真库有 72 种重复标题 —— 撞名时 `zipfile` 静默覆盖，不报错"""

    def test_duplicate_titles_get_distinct_filenames(self, test_db, stub_content):
        """★★ 三篇同名笔记必须产出三个文件，而不是一个

        真库实测：`标准适用范围` 出现 **9 次**。若不去重，用户导出 9 篇、
        解压后只剩 1 篇 —— 而**全程没有任何提示**。
        """
        asyncio.run(_seed_notes(test_db, [
            ("n-a", "同名笔记", "/u1/a.clean.md", "/u1/a.md"),
            ("n-b", "同名笔记", "/u1/b.clean.md", "/u1/b.md"),
            ("n-c", "同名笔记", "/u1/c.clean.md", "/u1/c.md"),
        ]))

        zf = _zip_of(_client().get("/api/notes/export", headers=_auth("u1")))
        names = [n for n in zf.namelist() if n != "index.md"]

        assert len(names) == 3, f"同名笔记被覆盖了：{names}"
        assert len(set(names)) == 3, "文件名仍有重复"
        assert "同名笔记.md" in names
        assert "同名笔记 (2).md" in names
        assert "同名笔记 (3).md" in names

    def test_dedup_is_deterministic(self, test_db, stub_content):
        """同样输入两次导出，文件名一致（顺序取决于查询顺序，必须稳定）"""
        asyncio.run(_seed_notes(test_db, [
            ("n-a", "同名", "/u1/a.clean.md", "/u1/a.md"),
            ("n-b", "同名", "/u1/b.clean.md", "/u1/b.md"),
        ]))

        client = _client()
        first = sorted(_zip_of(client.get("/api/notes/export", headers=_auth("u1"))).namelist())
        second = sorted(_zip_of(client.get("/api/notes/export", headers=_auth("u1"))).namelist())
        assert first == second


class TestSkipAndLimits:
    def test_note_without_the_requested_version_is_skipped_not_fatal(
        self, test_db, stub_content
    ):
        """★ 一篇没有清洗版 ⇒ **跳过它**，其余照常导出

        反过来（整包失败）会让用户在一堆笔记里逐个试错是哪篇的问题。
        """
        asyncio.run(_seed_notes(test_db, [
            ("n-a", "A 笔记", "/u1/a.clean.md", "/u1/a.md"),
            ("n-b", "B 笔记", None, "/u1/b.md"),  # 没有清洗版
        ]))

        zf = _zip_of(_client().get("/api/notes/export", headers=_auth("u1")))
        names = zf.namelist()

        assert "A 笔记.md" in names
        assert "B 笔记.md" not in names, "没有该版本正文的笔记却建了文件"

    def test_skipped_count_is_visible_in_index_and_header(self, test_db, stub_content):
        """★★ 跳过**不能是黑箱**：index.md 与响应头都要说清跳了几篇

        静默跳过比失败更糟 —— 用户以为"我的库就这么多"。
        """
        asyncio.run(_seed_notes(test_db, [
            ("n-a", "A 笔记", "/u1/a.clean.md", "/u1/a.md"),
            ("n-b", "B 笔记", None, "/u1/b.md"),
        ]))

        resp = _client().get("/api/notes/export", headers=_auth("u1"))
        assert resp.headers.get("X-Export-Skipped") == "1", resp.headers

        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        index = zf.read("index.md").decode("utf-8")
        assert "跳过" in index, index

    def test_nothing_exportable_returns_400(self, test_db, stub_content):
        """一篇都没有该版本 ⇒ 400（而不是给一个只有 index.md 的空包）"""
        asyncio.run(_seed_notes(test_db, [
            ("n-a", "A 笔记", None, "/u1/a.md"),
        ]))

        resp = _client().get("/api/notes/export", headers=_auth("u1"))

        assert resp.status_code == 400, resp.text
        assert resp.json()["error_code"] == "NOTE_EXPORT_ZIP_EMPTY"

    def test_explicit_ids_only_export_those(self, test_db, stub_content):
        """传 `ids` ⇒ 只导这几篇"""
        asyncio.run(_seed_notes(test_db, [
            ("n-a", "A 笔记", "/u1/a.clean.md", "/u1/a.md"),
            ("n-b", "B 笔记", "/u1/b.clean.md", "/u1/b.md"),
        ]))

        zf = _zip_of(
            _client().get("/api/notes/export?ids=n-a", headers=_auth("u1"))
        )
        names = zf.namelist()
        assert "A 笔记.md" in names
        assert "B 笔记.md" not in names

    def test_other_users_notes_are_not_exported(self, test_db, stub_content):
        """★ 归属校验：别人的 id 传进来也不会导出（跳过，不泄露内容）"""
        from app.models.user import User

        asyncio.run(_seed_notes(test_db, [
            ("n-a", "A 笔记", "/u1/a.clean.md", "/u1/a.md"),
        ]))

        async def _add_other():
            async with test_db() as session:
                session.add(User(id="u2", email="b@x.c", username="b", hashed_password="x"))
                await session.commit()

        asyncio.run(_add_other())

        resp = _client().get("/api/notes/export?ids=n-a", headers=_auth("u2"))

        # 别人的笔记查不到 ⇒ 一篇都没有 ⇒ 400（而不是把内容给出去）
        assert resp.status_code == 400, resp.text

    def test_trashed_notes_are_excluded(self, test_db, stub_content):
        """回收站里的笔记不导出（与其余端点同一口径）"""
        from sqlalchemy import select

        from app.models.note import Note

        asyncio.run(_seed_notes(test_db, [
            ("n-a", "A 笔记", "/u1/a.clean.md", "/u1/a.md"),
        ]))

        async def _trash():
            async with test_db() as session:
                note = (await session.execute(
                    select(Note).where(Note.id == "n-a")
                )).scalar_one()
                note.trashed_at = datetime.now(UTC)
                await session.commit()

        asyncio.run(_trash())

        resp = _client().get("/api/notes/export", headers=_auth("u1"))
        assert resp.status_code == 400, "回收站里的笔记被导出了"


class TestZipHelpers:
    """纯函数层（`build_zip_index` / `unique_zip_filename`）"""

    def test_index_escapes_pipes_in_titles(self):
        """标题里的 `|` 会破坏 Markdown 表格"""
        from app.services.note_export import build_zip_index

        md = build_zip_index(
            [{"filename": "a.md", "title": "甲|乙", "source_type": "pdf", "status": "cleaned"}],
            variant="clean",
        )
        assert "\\|" in md

    def test_index_url_encodes_filenames_with_spaces(self):
        """文件名里有空格时必须百分号编码，否则链接断在第一个空格处"""
        from app.services.note_export import build_zip_index

        md = build_zip_index(
            [{"filename": "a b.md", "title": "t", "source_type": "pdf", "status": "cleaned"}],
            variant="clean",
        )
        assert "%20" in md

    def test_unique_names_append_counters(self):
        from app.services.note_export import unique_zip_filename

        used: set = set()
        assert unique_zip_filename("同名", used) == "同名.md"
        assert unique_zip_filename("同名", used) == "同名 (2).md"
        assert unique_zip_filename("同名", used) == "同名 (3).md"

    def test_unique_names_sanitize_illegal_chars(self):
        """非法字符由既有的 `sanitize_filename` 处理（不重复实现一遍）"""
        from app.services.note_export import unique_zip_filename

        used: set = set()
        name = unique_zip_filename("a/b:c", used)
        assert "/" not in name and ":" not in name

    def test_zip_filename_uses_uuid_fallback_when_title_is_empty(self):
        """标题为空时用 id 兜底（否则所有空标题会撞成同一个名字）"""
        from app.services.note_export import unique_zip_filename

        used: set = set()
        nid = uuid.uuid4().hex[:8]
        name = unique_zip_filename("", used, fallback_id=nid)
        assert name.endswith(".md")
        assert nid in name
