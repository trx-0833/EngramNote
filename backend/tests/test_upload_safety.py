"""上传安全护栏测试（overhaul-plan 阶段 6.4）

## 这份测试要证明什么

三类护栏都防的是**恶意或畸形输入**，而它们的共同点是"正常使用时永远看不到" ——
所以判据必须钉在边界上，否则写了等于没写：

| 要证明的事 | 对应测试 |
|---|---|
| 合法 Office 文档**不被误伤** | `test_normal_docx_passes` |
| 高压缩比（zip 炸弹）被拒 | `test_high_ratio_is_rejected` |
| 解压后体积超限被拒 | `test_oversized_uncompressed_is_rejected` |
| 条目数超限被拒 | `test_too_many_entries_is_rejected` |
| **检查自身不解压**（否则检查就是帮攻击者解压） | `test_check_does_not_decompress` |
| 畸形 zip 按损坏拒绝而不是抛异常 | `test_broken_zip_is_rejected_not_raised` |
| 阈值 0 = 不启用该条 | `test_zero_threshold_disables_that_check` |
| PDF 页数上限 / 笔记数上限 | `TestPdfPages` / `TestNoteCount` |
| 三条护栏真的接在路由上 | `TestWiring` |
"""

import io
import zipfile
from pathlib import Path

import pytest

from app.services.upload_safety import (
    ArchiveStats,
    archive_stats,
    check_archive,
    check_note_count,
    check_pdf_pages,
)


def _make_zip(path: Path, *, entries: int = 1, payload: bytes = b"hello",
              compress: bool = True) -> Path:
    """造一个真实 zip 文件"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED) as zf:
        for i in range(entries):
            zf.writestr(f"word/document{i}.xml", payload)
    return path


def _make_bomb(path: Path, *, size_mb: int = 20) -> Path:
    """造一个"高压缩比"的 zip：内容全是 0，压缩后极小"""
    payload = b"\x00" * (size_mb * 1024 * 1024)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", payload)
    return path


class TestArchiveStats:
    def test_stats_read_from_central_directory(self, tmp_path):
        path = _make_zip(tmp_path / "a.docx", payload=b"x" * 1000)
        stats = archive_stats(path)
        assert isinstance(stats, ArchiveStats)
        assert stats.entries == 1
        assert stats.total_uncompressed_bytes == 1000
        assert stats.compression_ratio > 0

    def test_broken_zip_returns_none(self, tmp_path):
        path = tmp_path / "broken.docx"
        path.write_bytes(b"PK\x03\x04 not really a zip")
        assert archive_stats(path) is None


class TestCheckArchive:
    def test_normal_docx_passes(self, tmp_path):
        """★ 合法文档不得被误伤（护栏最常见的失败方式是把正常输入挡在门外）"""
        path = _make_zip(tmp_path / "ok.docx", entries=10, payload=b"<xml>" * 500)
        reason = check_archive(
            path, max_uncompressed_mb=500, max_compression_ratio=100, max_entries=5000,
        )
        assert reason is None, f"正常 Office 文档被拒了: {reason}"

    def test_high_ratio_is_rejected(self, tmp_path):
        """★ 压缩炸弹：20MB 全零内容压成几 KB"""
        path = _make_bomb(tmp_path / "bomb.docx", size_mb=20)
        reason = check_archive(
            path, max_uncompressed_mb=500, max_compression_ratio=100, max_entries=5000,
        )
        assert reason is not None
        assert "压缩比" in reason or "体积" in reason
        assert "拒绝" in reason

    def test_oversized_uncompressed_is_rejected(self, tmp_path):
        path = _make_zip(tmp_path / "big.docx", payload=b"\x00" * (5 * 1024 * 1024))
        reason = check_archive(
            path, max_uncompressed_mb=1, max_compression_ratio=0, max_entries=0,
        )
        assert reason is not None and "解压后体积过大" in reason

    def test_too_many_entries_is_rejected(self, tmp_path):
        path = _make_zip(tmp_path / "many.docx", entries=50, payload=b"x")
        reason = check_archive(
            path, max_uncompressed_mb=0, max_compression_ratio=0, max_entries=10,
        )
        assert reason is not None and "文件数量过多" in reason

    def test_broken_zip_is_rejected_not_raised(self, tmp_path):
        """畸形 zip 要**拒绝**（返回理由），而不是把 BadZipFile 抛给调用方"""
        path = tmp_path / "broken.docx"
        path.write_bytes(b"PK\x03\x04\x00\x00broken")
        reason = check_archive(
            path, max_uncompressed_mb=500, max_compression_ratio=100, max_entries=5000,
        )
        assert reason is not None and "损坏" in reason

    def test_zero_threshold_disables_that_check(self, tmp_path):
        """0 = 不启用该条（便于单独关掉某一条而不动其它）"""
        path = _make_bomb(tmp_path / "bomb.docx", size_mb=20)
        assert check_archive(
            path, max_uncompressed_mb=0, max_compression_ratio=0, max_entries=0,
        ) is None

    def test_check_does_not_decompress(self, tmp_path, monkeypatch):
        """★ 检查**不得**解压：否则"检查"就成了"帮攻击者解压"

        用 `ZipFile.read`/`open` 被调用来判定 —— 只允许读中央目录
        （`infolist`）。这条断言是护栏自身的安全边界。
        """
        path = _make_bomb(tmp_path / "bomb.docx", size_mb=5)
        called = {"read": False}

        real_read = zipfile.ZipFile.read
        real_open = zipfile.ZipFile.open

        def spy_read(self, *a, **k):
            called["read"] = True
            return real_read(self, *a, **k)

        def spy_open(self, *a, **k):
            called["read"] = True
            return real_open(self, *a, **k)

        monkeypatch.setattr(zipfile.ZipFile, "read", spy_read)
        monkeypatch.setattr(zipfile.ZipFile, "open", spy_open)
        archive_stats(path)
        assert called["read"] is False, "检查过程中解压了内容（zip 炸弹检查不能解压）"


class TestPdfPages:
    def test_over_limit_rejected(self):
        reason = check_pdf_pages(3000, max_pages=2000)
        assert reason is not None and "3000" in reason and "2000" in reason

    def test_at_limit_passes(self):
        assert check_pdf_pages(2000, max_pages=2000) is None

    def test_unknown_page_count_passes(self):
        """页数拿不到时不该拦（拦截要基于事实，不能基于猜测）"""
        assert check_pdf_pages(None, max_pages=10) is None

    def test_zero_means_unlimited(self):
        assert check_pdf_pages(999999, max_pages=0) is None


class TestNoteCount:
    def test_at_limit_rejected(self):
        reason = check_note_count(5000, max_notes=5000)
        assert reason is not None and "上限" in reason

    def test_below_limit_passes(self):
        assert check_note_count(4999, max_notes=5000) is None

    def test_zero_means_unlimited(self):
        assert check_note_count(10**9, max_notes=0) is None


class TestWiring:
    """三条护栏必须真的接在路由上（纯函数写得再对，不接线也等于没有）"""

    @staticmethod
    def _src() -> str:
        src = Path("app/api/upload.py").read_text(encoding="utf-8")
        # 剥掉注释：解释"这里为什么检查 X"的注释本身包含 X，会让断言自绊（附录 AI.5 同类）
        return "\n".join(line.split("#", 1)[0] for line in src.splitlines())

    def test_archive_check_wired(self):
        src = self._src()
        assert "check_archive(" in src
        assert "ARCHIVE_EXTS" in src

    def test_pdf_pages_wired(self):
        assert "check_pdf_pages(" in self._src()

    def test_note_count_wired(self):
        assert "check_note_count(" in self._src()

    def test_config_defaults_exist(self):
        from app.config import get_settings

        cfg = get_settings()
        assert cfg.max_pdf_pages > 0
        assert cfg.max_archive_uncompressed_mb > 0
        assert cfg.max_archive_compression_ratio > 0
        assert cfg.max_archive_entries > 0
        assert cfg.max_notes_per_user > 0


@pytest.mark.asyncio
class TestRouteRejectsBomb:
    """端到端：走真实路由，上传一个"炸弹型" docx 必须被拒"""

    async def test_prepare_upload_rejects_bomb(self, monkeypatch, tmp_path):
        from app.api import upload as upload_mod
        from app.models.user import User

        bomb = _make_bomb(tmp_path / "bomb.docx", size_mb=20)

        user = User(id="u-bomb", email="b@e.com", username="b", hashed_password="x")
        # 用真实的 UploadFile（`_stream_upload` 走的是它的 async read）
        upload = upload_mod.UploadFile(filename="bomb.docx", file=io.BytesIO(bomb.read_bytes()))
        with pytest.raises(upload_mod.HTTPException) as exc:
            await upload_mod.prepare_upload(file=upload, current_user=user)

        assert exc.value.status_code == 400
        detail = str(exc.value.detail)
        assert "压缩比" in detail or "体积" in detail
        # 拒绝时要顺手清掉临时目录（否则被拒的文件仍然占着磁盘）
        leftover = list((upload_mod.TMP_UPLOAD_DIR).glob("*")) if upload_mod.TMP_UPLOAD_DIR.exists() else []
        assert all("bomb.docx" not in str(p) for p in leftover), "被拒的上传留下了临时文件"
