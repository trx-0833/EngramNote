# -*- coding: utf-8 -*-
"""本地 MinerU 产物解压：md / layout / **插图** 都要落地（2026-11-09）

## 为什么这一组用构造的 zip 而不是真跑 MinerU

真实本地解析要 GB 级模型与几十秒 CPU 推理，CI 里跑不了。而这里的缺陷
与模型无关 —— 是"解压时只挑了两个文件"这个纯逻辑问题：

```
原实现：md_name = ...markdown.md ；middle_name = ...middle_json.json
        → 只写这两个，`images/*.jpg` 连落盘都没有
```

所以用一个**手工构造的 zip**（结构与 `mineru-kit parse --format zip` 一致）
就能守住：md、layout、插图都出来，`images/` 之外的图片不出来，
`..` 与绝对路径被拒。
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.services.mineru.converter import _extract_local_zip_artifacts

JPEG = b"\xff\xd8\xff\xe0fake-jpeg"


@pytest.fixture
def mineru_zip(tmp_path: Path) -> Path:
    """造一个与真实产物同形的 zip：markdown.md + middle_json.json + images/"""
    zip_path = tmp_path / "paper.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("paper/markdown.md", "# 标题\n\n![](images/fig-1.jpg)\n\n图注\n")
        zf.writestr("paper/middle_json.json", '{"pages": [1, 2]}')
        zf.writestr("paper/images/fig-1.jpg", JPEG)
        zf.writestr("paper/images/other.png", b"\x89PNG\r\n\x1a\nfake")
        # 白名单外 / images 之外：都不该被取出
        zf.writestr("paper/images/evil.svg", "<svg onload=alert(1)></svg>")
        zf.writestr("paper/root.png", b"\x89PNG\r\n\x1a\nroot")
        # 路径穿越：只该被拒，不该写到 tmp 之外
        zf.writestr("images/../../escape.jpg", JPEG)
    return zip_path


class TestLocalZipArtifacts:
    def test_markdown_and_layout_are_written(self, mineru_zip: Path, tmp_path: Path):
        out = tmp_path / "out"
        out.mkdir()
        _extract_local_zip_artifacts(mineru_zip, out, "paper")

        md = out / "paper.md"
        assert md.exists(), "markdown 没被写出来"
        assert "图注" in md.read_text(encoding="utf-8")
        assert (out / "layout.json").exists()

    def test_images_are_extracted(self, mineru_zip: Path, tmp_path: Path):
        """★ 本轮的判据：插图必须落盘，且内容逐字节一致"""
        out = tmp_path / "out"
        out.mkdir()
        _extract_local_zip_artifacts(mineru_zip, out, "paper")

        image = out / "paper" / "images" / "fig-1.jpg"
        assert image.exists(), "插图没有落盘（这正是页面只剩 alt 的根因）"
        assert image.read_bytes() == JPEG
        assert (out / "paper" / "images" / "other.png").exists()

    def test_only_whitelisted_images(self, mineru_zip: Path, tmp_path: Path):
        out = tmp_path / "out"
        out.mkdir()
        _extract_local_zip_artifacts(mineru_zip, out, "paper")

        assert not (out / "paper" / "images" / "evil.svg").exists(), "svg 不该被取出（同源加载=存储型 XSS）"
        assert not (out / "paper" / "root.png").exists(), "`images/` 之外的图片不是插图"

    def test_path_traversal_is_rejected(self, mineru_zip: Path, tmp_path: Path):
        out = tmp_path / "out"
        out.mkdir()
        _extract_local_zip_artifacts(mineru_zip, out, "paper")

        assert not (tmp_path / "escape.jpg").exists(), "路径穿越被写到了 output_dir 之外"
        assert not (out.parent / "escape.jpg").exists()

    def test_broken_zip_does_not_raise(self, tmp_path: Path):
        """zip 损坏时只 warning —— 随后 `rglob` 会报"没有产物"，不该在这里炸"""
        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"not a zip")
        out = tmp_path / "out"
        out.mkdir()
        _extract_local_zip_artifacts(bad, out, "x")  # 不抛即通过
        assert list(out.iterdir()) == []
