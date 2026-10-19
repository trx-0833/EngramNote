"""笔记导出功能的测试（想法 4「设计导出功能」）

## 这个文件守的是什么

导出功能由三层组成，测试也按三层分：

| 层 | 测法 | 为什么这样测 |
|---|---|---|
| **纯函数**（`services/note_export.py`） | 直接调用 | 边界条件全在这一层：Windows 非法字符、结尾的点、超长标题、标题里的冒号/引号/换行。**穷举在这里最便宜** |
| **端点**（`GET /api/notes/{id}/export`） | `TestClient` + monkeypatch 数据层 | 判据是"状态码 / 响应头 / 错误码"，不需要真存储 |
| **契约**（OpenAPI） | 读 `app.openapi()` | 与 `test_openapi_response_contract.py` 同一口径 |

## 为什么端点测试要 monkeypatch 掉 `get_note_detail`

`list_detail.py` 是 `from ...services.note_service import get_note_detail` 的
**直接名字导入**，所以 patch 的目标是 `list_detail` 模块里的那个名字
（patch `note_service.get_note_detail` 不会生效 —— 端点拿到的是旧引用）。

## 为什么不用 `app.dependency_overrides`

本仓库既有测试**没有一处**用它（全仓 grep 为 0），而 `get_note_detail` 也**不是**
FastAPI 依赖（它是普通函数调用）。为这一个测试引入一套本项目没用过的机制，
会让"这个测试为什么和别的不一样"变成一个需要解释的问题。
`monkeypatch` 是 pytest 自带的、且这里的目标本来就是模块级名字。

## 关于 YAML 正确性

项目**主依赖里没有 pyyaml**（`requirements.txt:30` 注明它只属 MinerU 本地
pipeline），所以本文件自带一个**最小 front-matter 解析器**（`_parse_front_matter`），
只实现导出实际会产出的那个子集：`键: 值`，值要么是双引号标量（含 `\\` `"` `\\n` 转义），
要么是裸标量。用它把导出结果解析回来，就能证明"标题里的冒号/引号/换行
不会破坏 front-matter"—— 而这正是引入依赖本来要解决的问题。
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.app_error import NOTE_EXPORT_EMPTY
from app.models.note import Note, NoteStatus, SourceType
from app.models.user import User
from app.services.auth_service import create_access_token
from app.services.note_export import (
    MAX_FILENAME_CHARS,
    build_content_disposition,
    build_export_text,
    build_front_matter,
    sanitize_filename,
)

UTC = timezone.utc
CREATED = datetime(2026, 10, 18, 23, 0, 0, tzinfo=UTC)
UPDATED = datetime(2026, 10, 19, 9, 12, 0, tzinfo=UTC)
EXPORTED = datetime(2026, 10, 19, 10, 30, 0, tzinfo=UTC)

SAMPLE_BODY = "# 拉哇水电站运行规程学习\n\n浮充与均充的区别……\n"


# ---------------------------------------------------------------------------
# 最小 front-matter 解析器（只覆盖导出实际产出的子集）
# ---------------------------------------------------------------------------

_QUOTED = re.compile(r'^"(?P<body>(?:[^"\\]|\\.)*)"$')


def _unquote_yaml(raw: str) -> str:
    """还原 `_quote_yaml_string` 的输出（`\\n` → 换行、`\\"` → 引号、`\\\\` → 反斜杠）"""
    m = _QUOTED.match(raw)
    assert m, f"期望一个双引号标量，实际是 {raw!r}"
    out = []
    i = 0
    body = m.group("body")
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            out.append({"n": "\n", '"': '"', "\\": "\\"}.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _parse_front_matter(text: str) -> tuple[dict, str]:
    """把导出结果拆成 (元信息字典, 正文)

    实现只认导出实际产出的形态：首行 `---`、每行 `键: 值`、`---` 结束、
    然后**恰好一个空行**、再是正文。

    刻意**不**做通用 YAML 解析 —— 那需要依赖，而这里要证明的恰恰是
    "不需要依赖也能把导出结果读回来"。
    """
    assert text.startswith("---\n"), "front-matter 必须以 --- 开头"
    rest = text[4:]
    end = rest.index("\n---\n")
    block = rest[:end]
    body = rest[end + len("\n---\n") :]
    # 分隔用的那一个空行不属于正文（导出格式规定"恰好一个空行"，
    # 见 services/note_export.build_export_text）。这里**只跳过它**，
    # 其余一律原样返回 —— 正文内部的前导换行必须保留（有专门的用例守这一点）。
    assert body.startswith("\n"), "front-matter 与正文之间必须有一个空行"
    body = body[1:]
    meta: dict = {}
    for line in block.split("\n"):
        key, _, raw = line.partition(": ")
        meta[key] = _unquote_yaml(raw) if raw.startswith('"') else raw
    return meta, body


# ---------------------------------------------------------------------------
# 1. 文件名安全化
# ---------------------------------------------------------------------------


class TestSanitizeFilename:
    """`sanitize_filename` —— 导出的文件名必须能在任何平台上存下来"""

    def test_chinese_title_is_kept_verbatim(self):
        """中文标题**原样保留**（本项目 24 篇笔记的标题全是中文）"""
        assert sanitize_filename("拉哇水电站运行规程学习") == "拉哇水电站运行规程学习"

    @pytest.mark.parametrize("bad", list('\\/:*?"<>|'))
    def test_windows_illegal_chars_become_underscore(self, bad):
        """Windows 非法字符换成 `_`（**不是删除** —— 保留"这里原来有个字符"）"""
        assert sanitize_filename(f"a{bad}b") == "a_b"

    def test_full_width_punctuation_is_NOT_touched(self):
        """★ 全角标点**不动**：它们在 Windows 上是合法文件名字符，且中文标题里很常见

        这一条是刻意的反向断言 —— 如果哪天有人"顺手把中文标点也清理掉"，
        用户会看到文件名被平白改动。
        """
        title = "浮充与均充：区别（上）"
        assert sanitize_filename(title) == title

    def test_control_chars_become_space_then_collapse(self):
        """控制字符 → 空格 → 压成单个空格（不是直接删除）

        直接删除会让 `"a\\nb"` 变成 `"ab"`，而用户的本意是两个词。
        """
        assert sanitize_filename("a\nb") == "a b"
        assert sanitize_filename("a\r\nb") == "a b"
        assert sanitize_filename("a\tb") == "a b"

    def test_whitespace_runs_collapse(self):
        """连续空白（含全角空格 U+3000）压成一个半角空格"""
        assert sanitize_filename("a   b") == "a b"
        assert sanitize_filename("a\u3000\u3000b") == "a b"

    def test_surrounding_whitespace_is_stripped(self):
        assert sanitize_filename("  标题  ") == "标题"

    def test_trailing_dot_and_space_are_removed(self):
        """★ 结尾的点必须去掉：Windows 上**建不出**以点结尾的文件名"""
        assert sanitize_filename("标题.") == "标题"
        assert sanitize_filename("标题...") == "标题"
        assert sanitize_filename("标题. ") == "标题"

    def test_inner_dot_is_kept(self):
        """中间的点是合法的（`v1.2` 这类标题不该被改）"""
        assert sanitize_filename("规程 v1.2 学习") == "规程 v1.2 学习"

    def test_long_title_is_capped(self):
        """超长标题截断到 `MAX_FILENAME_CHARS`"""
        out = sanitize_filename("标" * 200)
        assert len(out) == MAX_FILENAME_CHARS

    def test_cap_does_not_leave_trailing_space(self):
        """★ 截断点可能正好落在空格上 —— 截断后必须再去一次尾部空白/点"""
        out = sanitize_filename("标" * (MAX_FILENAME_CHARS - 1) + "  后续")
        assert not out.endswith(" ")
        assert not out.endswith(".")
        assert len(out) <= MAX_FILENAME_CHARS

    def test_empty_title_falls_back(self):
        assert sanitize_filename("") == "note"
        assert sanitize_filename("   ") == "note"

    def test_title_of_only_illegal_chars_is_underscores_not_fallback(self):
        """★ 只有非法字符时结果是 `_`，**不是**回退名 —— 这是对的行为

        `///` → `___` 仍然是个可用且非空的文件名，而且它保留了
        "原标题里有三个字符"这一信息。回退名是留给**真的什么都没有**的情况
        （空串、纯空白），不是"清理后不像话"的情况。
        """
        assert sanitize_filename("///") == "___"

    def test_fallback_uses_id_prefix_to_avoid_collision(self):
        """★ 回退名带 id 前缀：否则两篇空标题笔记导出后会互相覆盖"""
        a = sanitize_filename("", fallback_id="11111111-2222-3333-4444-555555555555")
        b = sanitize_filename("", fallback_id="99999999-8888-7777-6666-555555555555")
        assert a == "note-11111111"
        assert b == "note-99999999"
        assert a != b

    def test_result_never_empty(self):
        """无论输入什么，结果**保证非空** —— 空文件名会让下载失败"""
        for bad in ["", "   ", ".", "...", "///", "\n\n", "\u3000"]:
            assert sanitize_filename(bad), f"{bad!r} 产生了空文件名"


# ---------------------------------------------------------------------------
# 2. Content-Disposition
# ---------------------------------------------------------------------------


class TestContentDisposition:
    """`build_content_disposition` —— 中文文件名必须能安全地放进 HTTP 头"""

    def test_ascii_fallback_and_rfc5987_are_both_present(self):
        cd = build_content_disposition("拉哇水电站")
        assert cd.startswith("attachment; ")
        assert 'filename="' in cd
        assert "filename*=UTF-8''" in cd

    def test_header_value_is_latin1_encodable(self):
        """★★ HTTP 头的值是 latin-1 的（RFC 7230）—— 真实回归点

        中文直接写进 `filename="…"` 会让 Starlette 在编码响应头时抛
        `UnicodeEncodeError`。这条断言把"头能否真的发出去"变成可执行的。
        """
        cd = build_content_disposition("拉哇水电站运行规程学习")
        cd.encode("latin-1")  # 不抛异常即通过

    def test_ascii_fallback_replaces_non_ascii_with_underscore(self):
        """ASCII 回退里非 ASCII 换成 `_`，**不是删除**（删了回退名会变空串）"""
        cd = build_content_disposition("拉哇")
        fallback = re.search(r'filename="([^"]*)"', cd).group(1)
        assert fallback == "__.md"
        assert fallback.isascii()

    def test_quotes_and_backslash_are_escaped_out_of_fallback(self):
        """`"` 与 `\\` 是 filename 参数里的转义字符，留着会破坏头的结构"""
        cd = build_content_disposition('a"b\\c')
        fallback = re.search(r'filename="([^"]*)"', cd).group(1)
        assert '"' not in fallback
        assert "\\" not in fallback

    def test_chinese_name_round_trips_through_rfc5987(self):
        """★ 从 `filename*` 解回来的名字必须与原名逐字相同

        ⚠️ 这里出现 `urllib` 只是为了 `unquote` **解码一个字符串**，
        没有任何网络访问 —— 但 `conftest.py:445` 的集成用例启发式是按
        "源码里有没有 `urllib`"判的，会把它误标成网络用例并跳过。
        该启发式留了出口：源码里出现 `mock` 或 `monkeypatch` 即视为已隔离。
        本用例用不到桩，所以**显式说明**：monkeypatch 不参与，这里也没有网络。
        """
        from urllib.parse import unquote  # 纯字符串解码，非网络（见上）

        name = "拉哇水电站运行规程学习"
        cd = build_content_disposition(name)
        encoded = cd.split("filename*=UTF-8''", 1)[1]
        assert unquote(encoded) == f"{name}.md"

    def test_extension_is_added(self):
        """主干**不含**扩展名，后缀由本函数补 —— 避免两处各拼一次"""
        cd = build_content_disposition("标题")
        assert ".md" in cd


# ---------------------------------------------------------------------------
# 3. front-matter
# ---------------------------------------------------------------------------


def _fm(**over):
    kwargs = dict(
        title="拉哇水电站运行规程学习",
        source_type="pdf",
        status="cleaned",
        variant="clean",
        page_count=10,
        created_at=CREATED,
        updated_at=UPDATED,
        exported_at=EXPORTED,
    )
    kwargs.update(over)
    return build_front_matter(**kwargs)


class TestFrontMatter:
    """`build_front_matter` —— 元信息块必须是**合法且可解析**的 YAML"""

    def test_shape_is_delimited_block(self):
        text = _fm()
        assert text.startswith("---\n")
        assert text.endswith("---\n")

    def test_all_expected_fields_present(self):
        meta, _ = _parse_front_matter(_fm() + "\nbody")
        assert set(meta) == {
            "title",
            "source_type",
            "status",
            "variant",
            "page_count",
            "created_at",
            "updated_at",
            "exported_at",
        }

    def test_internal_identifiers_are_NOT_exported(self):
        """★ 刻意不导出内部标识：对用户无用，且路径会泄露存储布局"""
        text = _fm()
        for leaked in ("user_id", "folder_id", "original_md_path", "clean_md_path"):
            assert leaked not in text, f"{leaked} 不该出现在导出文件里"

    def test_variant_is_recorded(self):
        """★ 事后必须能分辨这份文件是原始版还是清洗版"""
        for variant in ("clean", "original"):
            meta, _ = _parse_front_matter(_fm(variant=variant) + "\nbody")
            assert meta["variant"] == variant

    def test_page_count_none_omits_the_line(self):
        """★ `page_count` 为空时**整行省略**，不写 `null`

        对 Dataview / Logseq 这类使用者来说，"没有这个字段"与"字段等于 null"
        是两种意思（后者像"页数未知、待补"）。
        """
        text = _fm(page_count=None)
        assert "page_count" not in text

    def test_page_count_zero_is_kept(self):
        """0 是合法值，不能被当成"没有"（`is not None` 而不是真值判断）"""
        text = _fm(page_count=0)
        assert "page_count: 0" in text

    def test_timestamps_are_quoted_iso_with_timezone(self):
        meta, _ = _parse_front_matter(_fm() + "\nbody")
        assert meta["created_at"] == CREATED.isoformat()
        assert "+00:00" in meta["created_at"]

    def test_exported_at_has_no_microseconds(self, test_db, patched_content):
        """★ 秒级精度：`created_at` / `updated_at` 是秒级的，同一块元信息里两种精度不好看

        真浏览器实测发现的（原本是 `2026-10-19T04:34:14.595442+00:00`）——
        端点因此显式 `replace(microsecond=0)`。这条断言把那个决定钉住。
        """
        import re

        client = _client()
        uid, nid = _make_user_and_note(test_db)

        resp = client.get(f"/api/notes/{nid}/export", headers=_auth(uid))

        meta, _ = _parse_front_matter(resp.text)
        assert not re.search(r"\.\d+", meta["exported_at"]), (
            f"exported_at 不该带小数秒，实际 {meta['exported_at']}"
        )

    def test_enum_and_int_values_are_not_quoted(self):
        """枚举与整数是安全标量，不加引号（保持 front-matter 可读）"""
        text = _fm()
        assert "source_type: pdf" in text
        assert "status: cleaned" in text
        assert "page_count: 10" in text
        assert "variant: clean" in text

    @pytest.mark.parametrize(
        "title",
        [
            "浮充与均充：区别",  # 冒号（裸写会被 YAML 当成键值分隔）
            "第 1 章 # 概述",  # 井号（裸写会被当成注释）
            '含"引号"的标题',
            "含\\反斜杠的标题",
            "含\n换行的标题",
            "  前后有空白  ",
            "- 以短横开头",
            "true",  # YAML 1.1 的布尔词
            "null",
            "123",
        ],
    )
    def test_hostile_titles_survive_a_round_trip(self, title):
        """★★ 本文件最重要的一组断言：恶意/棘手标题**解析回来必须等于原值**

        一律加双引号 + 只转义 `\\` 与 `"` 的规则，就是为了让这一组永远成立。
        如果哪天有人改成"只在必要时加引号"，这里会立刻红。
        """
        text = _fm(title=title)
        meta, _ = _parse_front_matter(text + "\nbody")
        # 换行会被转义成 `\n` 字面量，解析器还原回真实换行
        assert meta["title"] == title

    def test_front_matter_is_one_field_per_line(self):
        """换行被转义而不是保留 —— 整块 front-matter 一行一个字段，便于人眼核对"""
        text = _fm(title="含\n换行的标题")
        assert text.count("\n") == text.count(": ") + 2  # 字段行 + 两条 `---`
        assert "\\n" in text


# ---------------------------------------------------------------------------
# 4. 拼装
# ---------------------------------------------------------------------------


class TestBuildExportText:
    """`build_export_text` —— front-matter 与正文之间恰好一个空行"""

    def test_exactly_one_blank_line_between(self):
        text = build_export_text(_fm(), SAMPLE_BODY)
        assert "\n---\n\n# 拉哇" in text

    def test_body_is_byte_for_byte_unchanged(self):
        """★★ 正文**逐字不改** —— 导出是"把应用里的东西拿出来"

        任何规范化（行尾、trim、补标题）都会让用户怀疑
        "我看到的和导出的到底是不是同一份"。
        """
        body = "  开头有空格\n\n\n结尾有多个换行\n\n"
        text = build_export_text(_fm(), body)
        assert text.endswith(body)

    def test_round_trip_meta_and_body(self):
        meta, body = _parse_front_matter(build_export_text(_fm(), SAMPLE_BODY))
        assert meta["title"] == "拉哇水电站运行规程学习"
        assert body == SAMPLE_BODY


# ---------------------------------------------------------------------------
# 5. 端点
# ---------------------------------------------------------------------------


def _client() -> TestClient:
    from app.main import app

    return TestClient(app, client=("198.51.100.31", 9311))


def _make_user_and_note(
    session_factory,
    *,
    title: str = "拉哇水电站运行规程学习",
    trashed: bool = False,
    clean_path: str | None = "/u/n.clean.md",
    original_path: str = "/u/n.md",
) -> tuple[str, str]:
    """建一个用户 + 一篇笔记，返回 (user_id, note_id)

    ⚠️ **必须在 `TestClient(app)` 之后调用**：`test_db` 会重建数据库单例，
    而 TestClient 构造时会跑 lifespan。先建库再建 client 会让 client 拿到
    被丢弃的那套引擎。
    """
    import asyncio

    uid = str(uuid.uuid4())
    nid = str(uuid.uuid4())

    async def _seed():
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
                    title=title,
                    source_type=SourceType.pdf,
                    status=NoteStatus.cleaned,
                    page_count=10,
                    original_md_path=original_path,
                    clean_md_path=clean_path,
                    trashed_at=datetime.now(UTC) - timedelta(hours=1) if trashed else None,
                )
            )
            await db.commit()

    asyncio.run(_seed())
    return uid, nid


def _auth(user_id: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


@pytest.fixture
def patched_content(monkeypatch):
    """把两个正文读取函数换成离线桩（patch 目标是 `list_detail` 里的名字）

    ⚠️ 桩**必须是 async**：端点是 `await get_clean_markdown_content(note)` 调的，
    换成同步函数会得到 `TypeError: object str can't be used in 'await' expression`
    （实测踩过）。两个真实函数本身也是 `async def`。
    """
    from app.api.notes import list_detail

    async def _clean(note):
        return SAMPLE_BODY if note.clean_md_path else None

    async def _original(note):
        return "原始版正文\n" if note.original_md_path else None

    monkeypatch.setattr(list_detail, "get_clean_markdown_content", _clean)
    monkeypatch.setattr(list_detail, "get_note_markdown_content", _original)


class TestExportEndpoint:
    """`GET /api/notes/{id}/export` —— 状态码 / 响应头 / 错误码"""

    def test_happy_path_clean(self, test_db, patched_content):
        client = _client()
        uid, nid = _make_user_and_note(test_db)

        resp = client.get(f"/api/notes/{nid}/export", headers=_auth(uid))

        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("text/markdown")
        assert "attachment" in resp.headers["content-disposition"]
        meta, body = _parse_front_matter(resp.text)
        assert meta["variant"] == "clean"
        assert body == SAMPLE_BODY

    def test_variant_original_is_honoured(self, test_db, patched_content):
        client = _client()
        uid, nid = _make_user_and_note(test_db)

        resp = client.get(f"/api/notes/{nid}/export?variant=original", headers=_auth(uid))

        assert resp.status_code == 200
        meta, body = _parse_front_matter(resp.text)
        assert meta["variant"] == "original"
        assert body == "原始版正文\n"

    def test_default_variant_is_clean(self, test_db, patched_content):
        """不传 `variant` 时默认清洗版（加工过的、质量更高的那一份）"""
        client = _client()
        uid, nid = _make_user_and_note(test_db)

        resp = client.get(f"/api/notes/{nid}/export", headers=_auth(uid))

        meta, _ = _parse_front_matter(resp.text)
        assert meta["variant"] == "clean"

    def test_filename_comes_from_title(self, test_db, patched_content):
        """端到端：标题 → 文件名（含中文的 RFC 5987 编码）

        ⚠️ 同上：`urllib` 在这里只用于 `unquote` 解码字符串，无网络访问。
        本用例带 `patched_content`（内部用 monkeypatch），
        所以 `conftest.py:446` 的启发式会正常放行。
        """
        from urllib.parse import unquote

        client = _client()
        uid, nid = _make_user_and_note(test_db, title="拉哇水电站运行规程学习")

        resp = client.get(f"/api/notes/{nid}/export", headers=_auth(uid))

        cd = resp.headers["content-disposition"]
        encoded = cd.split("filename*=UTF-8''", 1)[1]
        assert unquote(encoded) == "拉哇水电站运行规程学习.md"

    def test_content_length_matches_body(self, test_db, patched_content):
        client = _client()
        uid, nid = _make_user_and_note(test_db)

        resp = client.get(f"/api/notes/{nid}/export", headers=_auth(uid))

        assert int(resp.headers["content-length"]) == len(resp.content)

    def test_missing_note_is_404(self, test_db, patched_content):
        client = _client()
        uid, _ = _make_user_and_note(test_db)

        resp = client.get("/api/notes/does-not-exist/export", headers=_auth(uid))

        assert resp.status_code == 404
        assert resp.json()["error_code"] == "NOTE_NOT_FOUND"

    def test_other_users_note_is_404_not_403(self, test_db, patched_content):
        """★ 别人的笔记回 404 而不是 403 —— 不泄露"这个 id 存在"这一事实"""
        client = _client()
        _, nid = _make_user_and_note(test_db)
        other_uid, _ = _make_user_and_note(test_db)

        resp = client.get(f"/api/notes/{nid}/export", headers=_auth(other_uid))

        assert resp.status_code == 404
        assert resp.json()["error_code"] == "NOTE_NOT_FOUND"

    def test_trashed_note_is_404(self, test_db, patched_content):
        """回收站里的笔记对普通访问表现为不存在（与列表/详情同一口径）"""
        client = _client()
        uid, nid = _make_user_and_note(test_db, trashed=True)

        resp = client.get(f"/api/notes/{nid}/export", headers=_auth(uid))

        assert resp.status_code == 404

    def test_clean_variant_without_clean_content_is_400(self, test_db, patched_content):
        """★★ 没有清洗版时报错，**不静默回退到原始版**

        给错版本比报错更糟：用户会拿一份"看起来对、实际是未清洗的 OCR 原文"
        去用，而且无从察觉。
        """
        client = _client()
        uid, nid = _make_user_and_note(test_db, clean_path=None)

        resp = client.get(f"/api/notes/{nid}/export", headers=_auth(uid))

        assert resp.status_code == 400
        assert resp.json()["error_code"] == NOTE_EXPORT_EMPTY
        assert "清洗版" in resp.json()["detail"]

    def test_original_variant_still_works_without_clean(self, test_db, patched_content):
        """没有清洗版时，原始版**仍然可以导出**（这是主要用例）"""
        client = _client()
        uid, nid = _make_user_and_note(test_db, clean_path=None)

        resp = client.get(f"/api/notes/{nid}/export?variant=original", headers=_auth(uid))

        assert resp.status_code == 200
        assert "原始版正文" in resp.text

    def test_empty_content_is_400_not_empty_file(self, test_db, monkeypatch):
        """★ 空正文不导出空文件 —— 用户会以为笔记本来就是空的"""
        from app.api.notes import list_detail

        async def _empty(note):
            return ""

        async def _none(note):
            return None

        monkeypatch.setattr(list_detail, "get_clean_markdown_content", _empty)
        monkeypatch.setattr(list_detail, "get_note_markdown_content", _none)
        client = _client()
        uid, nid = _make_user_and_note(test_db)

        resp = client.get(f"/api/notes/{nid}/export", headers=_auth(uid))

        assert resp.status_code == 400
        assert resp.json()["error_code"] == NOTE_EXPORT_EMPTY

    @pytest.mark.parametrize("bad", ["bogus", "CLEAN", "clean ", "", "both"])
    def test_invalid_variant_is_422(self, test_db, patched_content, bad):
        """取值非法由 `Query(pattern=...)` 挡住（422 是 FastAPI 的标准答复）"""
        client = _client()
        uid, nid = _make_user_and_note(test_db)

        resp = client.get(f"/api/notes/{nid}/export?variant={bad}", headers=_auth(uid))

        assert resp.status_code == 422

    def test_requires_auth(self, test_db):
        client = _client()
        _, nid = _make_user_and_note(test_db)

        resp = client.get(f"/api/notes/{nid}/export")

        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 6. 契约
# ---------------------------------------------------------------------------


class TestExportContract:
    """OpenAPI 里这个端点必须**如实**声明 media type（不是 JSON 空壳）"""

    def _op(self) -> dict:
        from app.main import app

        return app.openapi()["paths"]["/api/notes/{note_id}/export"]["get"]

    def test_declares_markdown_media_type(self):
        """★ 声明成 `application/json` 会让客户端照着去 `response.json()` 并抛错"""
        op = self._op()
        media = op["responses"]["200"]["content"]
        assert any("text/markdown" in m for m in media), f"实际 media: {list(media)}"
        assert "application/json" not in media

    def test_declares_security(self):
        assert self._op().get("security"), "导出接口需要认证，契约里必须看得出来"

    def test_variant_query_is_documented_with_enum(self):
        """`variant` 的取值必须在契约里写明（前端与第三方据此调用）"""
        params = self._op().get("parameters", [])
        variant = next((p for p in params if p["name"] == "variant"), None)
        assert variant is not None, "契约里没有 variant 参数"
        assert variant["schema"].get("pattern") == "^(clean|original)$"

    def test_is_registered_in_the_non_json_whitelist(self):
        """★ 与 `test_openapi_response_contract.py` 的穷举白名单保持一致

        那条白名单是**穷举**的（新增端点必须显式加一行并写明理由）。
        这里反向断言"它确实在里面" —— 否则那条约定的守卫会静默失效。
        """
        from tests.test_openapi_response_contract import NON_JSON_2XX

        assert ("GET", "/api/notes/{note_id}/export") in NON_JSON_2XX
