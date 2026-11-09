# -*- coding: utf-8 -*-
"""笔记插图：落盘 / 搬家 / 删除 / 引用重写（2026-11-09）

## 这一组守什么

MinerU 解析带插图的 PDF 时，产物里既有 markdown（对图写**相对引用**
`![](images/xxx.jpg)`，位置就在原段落处），也有 `images/{sha}.jpg`。
2026-11-09 之前图片在三处丢掉：转换产物落在临时目录、本地解压只挑 md、
没有分发入口。这一组守前两处的补齐：

1. `persist_converted_images` 把 `images/` 下的图写进 vault（白名单外的跳过）；
2. `move_assets` / `delete_assets` 与 md 同步（回收站、恢复、彻底删除）；
3. `rewrite_asset_refs` 把正文里的相对引用改到另一个前缀下（导出 ZIP 用）；
4. `is_safe_asset_path` 挡住路径穿越与非图片后缀（端点据此回 415/404）。
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.services import note_assets, vault_path
from app.services.storage_service import file_exists


@pytest.fixture
def converted_dir(tmp_path: Path) -> Path:
    """造一份"MinerU 产物目录"：里面有真图、svg、以及 images/ 之外的文件"""
    out = tmp_path / "converted" / "paper"
    (out / "images").mkdir(parents=True)
    (out / "images" / "fig-1.jpg").write_bytes(b"\xff\xd8\xff\xe0JFIF-fake-jpeg")
    (out / "images" / "fig-2.png").write_bytes(b"\x89PNG\r\n\x1a\nfake-png")
    # 非白名单后缀：**不该**被落盘（svg 可内嵌脚本）
    (out / "images" / "evil.svg").write_text("<svg onload=alert(1)></svg>", encoding="utf-8")
    # images/ 之外的文件：不算插图
    (tmp_path / "converted" / "root.png").write_bytes(b"\x89PNG\r\n\x1a\nroot")
    (out / "full.md").write_text("![](images/fig-1.jpg)\n", encoding="utf-8")
    return out


class TestPersistConvertedImages:
    def test_writes_whitelisted_images_only(self, test_db, converted_dir):
        user_id = str(uuid.uuid4())
        prefix = vault_path.inbox_prefix(user_id)
        base = "paper"

        saved = note_assets.persist_converted_images(converted_dir, prefix, base)

        assert saved == 2, "只该写入 png/jpg 两张（svg 与 images/ 之外的文件都不算）"
        md_bucket = note_assets._markdown_bucket()
        assert file_exists(md_bucket, vault_path.assets_object(prefix, base, "images/fig-1.jpg"))
        assert file_exists(md_bucket, vault_path.assets_object(prefix, base, "images/fig-2.png"))
        assert not file_exists(md_bucket, vault_path.assets_object(prefix, base, "images/evil.svg"))

    def test_reads_back_the_bytes(self, test_db, converted_dir):
        prefix = vault_path.inbox_prefix(str(uuid.uuid4()))
        note_assets.persist_converted_images(converted_dir, prefix, "paper")

        data = note_assets.read_asset(prefix, "paper", "images/fig-1.jpg")
        assert data == b"\xff\xd8\xff\xe0JFIF-fake-jpeg"

    def test_empty_dir_is_noop(self, tmp_path: Path):
        prefix = vault_path.inbox_prefix(str(uuid.uuid4()))
        assert note_assets.persist_converted_images(tmp_path, prefix, "nothing") == 0


class TestMoveAndDelete:
    def test_move_assets_keeps_relative_paths(self, test_db, converted_dir):
        user_id = str(uuid.uuid4())
        inbox = vault_path.inbox_prefix(user_id)
        trash = vault_path.trash_prefix(user_id, "note-1")
        note_assets.persist_converted_images(converted_dir, inbox, "paper")

        moved = note_assets.move_assets(inbox, trash, "paper")

        assert moved == 2
        assert note_assets.read_asset(trash, "paper", "images/fig-1.jpg") is not None
        assert note_assets.read_asset(inbox, "paper", "images/fig-1.jpg") is None

    def test_delete_assets_removes_all(self, test_db, converted_dir):
        user_id = str(uuid.uuid4())
        inbox = vault_path.inbox_prefix(user_id)
        note_assets.persist_converted_images(converted_dir, inbox, "paper")

        deleted = note_assets.delete_assets(inbox, "paper")

        assert deleted == 2
        assert list(note_assets.list_asset_objects(inbox, "paper")) == []


class TestSafety:
    @pytest.mark.parametrize(
        "path",
        [
            "images/ok.png",
            "images/2024/ok.JPG",
            "images/ok.webp",
            "images/ok.gif",
            "images/ok.jpeg",
        ],
    )
    def test_accepts_whitelisted_relative_paths(self, path):
        assert note_assets.is_safe_asset_path(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "",
            "   ",
            "../secret.png",
            "images/../../secret.png",
            "/etc/passwd.png",
            "C:/windows/x.png",
            "images/evil.svg",
            "images/note.txt",
            "images/no-extension",
        ],
    )
    def test_rejects_unsafe_or_non_image(self, path):
        assert note_assets.is_safe_asset_path(path) is False

    def test_backslash_is_normalized_not_rejected(self):
        """`images\\x.png` 归一成 `images/x.png` 后仍在资产目录内 —— 接纳比拒绝更合适

        （归一化之后就不存在"它到底在不在目录里"的歧义；归一化逻辑与
        `normalize_rel_path` 共用一套判据。）
        """
        assert note_assets.is_safe_asset_path("images\\x.png") is True
        assert note_assets.normalize_rel_path("images\\x.png") == "images/x.png"

    def test_read_asset_refuses_unsafe_path(self, test_db, converted_dir):
        prefix = vault_path.inbox_prefix(str(uuid.uuid4()))
        note_assets.persist_converted_images(converted_dir, prefix, "paper")
        assert note_assets.read_asset(prefix, "paper", "../images/fig-1.jpg") is None

    def test_content_type_mapping(self):
        assert note_assets.content_type_for("a.png") == "image/png"
        assert note_assets.content_type_for("a.JPG") == "image/jpeg"
        assert note_assets.content_type_for("a.svg") is None


class TestRewriteAssetRefs:
    def test_relative_markdown_image_is_prefixed(self):
        md = "前文\n\n![](images/fig-1.jpg)\n\n图 1：说明\n"
        out = note_assets.rewrite_asset_refs(md, "assets/paper/")
        assert "![](assets/paper/images/fig-1.jpg)" in out
        assert "图 1：说明" in out

    def test_absolute_urls_untouched(self):
        md = "![a](https://example.com/x.png) ![b](//cdn/x.png) ![c](/root/x.png) ![d](#frag)"
        assert note_assets.rewrite_asset_refs(md, "assets/paper/") == md

    def test_html_img_src_is_prefixed(self):
        out = note_assets.rewrite_asset_refs('<img src="images/x.png" alt="x">', "assets/paper/")
        assert 'src="assets/paper/images/x.png"' in out

    def test_title_in_markdown_image_is_kept(self):
        out = note_assets.rewrite_asset_refs('![a](images/x.png "图一")', "assets/paper/")
        assert out == '![a](assets/paper/images/x.png "图一")'
