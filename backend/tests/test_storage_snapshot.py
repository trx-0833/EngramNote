"""对象存储快照测试（阶段 6.6 的第三项，附录 AW）

覆盖重点不是"复制成功"，而是**这份快照值不值得信**：

- 清单必须记录 sha256，且复制前后比对（撕裂的副本不进清单）；
- 快照不完整时 `ok=False`，但**不抛异常**（不阻塞 Beat）；
- 保留策略**只删自己的目录** —— 绝不能碰数据库快照与手工备份；
- 恢复默认**只补缺失文件**，且**绝不删除**目标目录里多出来的内容；
- `inspect` 能分辨缺失 / 大小不符 / 哈希不符三种问题。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from app.services import storage_snapshot as ss


def _make_tree(root: Path, files: dict[str, bytes]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


@pytest.fixture
def tree(tmp_path):
    """一套临时存储目录 + 临时备份目录（绝不碰真实 data/storage 与 _backup）"""
    storage = tmp_path / "storage"
    backup = tmp_path / "backup"
    backup.mkdir()
    _make_tree(storage, {
        "u1/n1/n1.pdf": b"%PDF-1.4 fake",
        "u1/n1/n1.md": "# 标题\n正文".encode(),
        "u2/n2/n2.json": json.dumps({"a": 1}).encode(),
        "u2/n2/n2.mp4": b"\x00" * 40,
    })
    return storage, backup


# ===========================================================================
# 创建：清单 / 哈希 / 返回体
# ===========================================================================

class TestCreateSnapshot:
    def test_creates_snapshot_with_manifest(self, tree):
        storage, backup = tree
        result = ss.create_storage_snapshot(
            storage_root=storage, backup_root=backup, keep=3,
            now=datetime(2026, 9, 12, 4, 45, 0),
        )
        assert result["ok"] is True
        assert result["files"] == 4
        assert result["bytes"] == sum(
            p.stat().st_size for p in storage.rglob("*") if p.is_file()
        )

        snapshot = Path(result["snapshot"])
        assert snapshot.name == "20260912-044500-storage"
        assert ss.is_storage_snapshot(snapshot)

        manifest = json.loads(
            (snapshot / ss.MANIFEST_FILENAME).read_text(encoding="utf-8")
        )
        assert manifest["kind"] == "storage"
        assert manifest["files_count"] == 4
        assert set(manifest["files"]) == {
            "u1/n1/n1.pdf", "u1/n1/n1.md", "u2/n2/n2.json", "u2/n2/n2.mp4",
        }
        assert manifest["unstable"] == [] and manifest["failed"] == []

    def test_manifest_records_real_sha256_of_source(self, tree):
        """清单里的哈希必须是**源文件**的哈希，而不是随便什么值"""
        import hashlib

        storage, backup = tree
        result = ss.create_storage_snapshot(
            storage_root=storage, backup_root=backup, keep=3
        )
        snapshot = Path(result["snapshot"])
        manifest = json.loads(
            (snapshot / ss.MANIFEST_FILENAME).read_text(encoding="utf-8")
        )

        src = storage / "u1/n1/n1.md"
        expected = hashlib.sha256(src.read_bytes()).hexdigest()
        assert manifest["files"]["u1/n1/n1.md"]["sha256"] == expected
        # 副本与源逐字节相同
        assert (snapshot / "u1/n1/n1.md").read_bytes() == src.read_bytes()

    def test_missing_storage_root_is_not_a_failure(self, tmp_path):
        """全新部署还没上传过文件：跳过而不是报错"""
        result = ss.create_storage_snapshot(
            storage_root=tmp_path / "nope", backup_root=tmp_path / "backup", keep=3
        )
        assert result["ok"] is True
        assert result["files"] == 0
        assert result["snapshot"] is None
        assert "不存在" in result["reason"]

    def test_same_second_collision_is_reported_not_overwritten(self, tree):
        """同一秒内重复执行不能把已有快照覆盖掉（那是一份救命备份）"""
        storage, backup = tree
        now = datetime(2026, 9, 12, 4, 45, 0)
        first = ss.create_storage_snapshot(
            storage_root=storage, backup_root=backup, keep=3, now=now
        )
        (Path(first["snapshot"]) / "u1/n1/n1.md").write_bytes("被改过".encode())

        second = ss.create_storage_snapshot(
            storage_root=storage, backup_root=backup, keep=3, now=now
        )
        assert second["ok"] is False
        assert "已存在" in second["reason"]
        # 已有快照保持**原样**（未被覆盖重建）：刚写进去的标记仍在
        assert (Path(first["snapshot"]) / "u1/n1/n1.md").read_bytes() == "被改过".encode()

    def test_unstable_file_is_excluded_from_manifest_and_reported(
        self, tree, monkeypatch
    ):
        """复制后哈希不符 → 不进清单、计入 unstable、整体 ok=False（绝不静默）"""
        storage, backup = tree

        calls: list[Path] = []
        real_copy = ss._copy_verified

        def flaky(src: Path, dst: Path):
            # 让 n1.md 每次都"复制后校验失败"
            if src.name == "n1.md":
                calls.append(src)
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(b"torn")
                return False, "复制后哈希不一致（源文件在复制期间被改写或磁盘异常）"
            return real_copy(src, dst)

        monkeypatch.setattr(ss, "_copy_verified", flaky)
        result = ss.create_storage_snapshot(
            storage_root=storage, backup_root=backup, keep=3
        )

        assert result["ok"] is False
        assert [u["path"] for u in result["unstable"]] == ["u1/n1/n1.md"]
        assert result["files"] == 3
        manifest = json.loads(
            (Path(result["snapshot"]) / ss.MANIFEST_FILENAME).read_text(encoding="utf-8")
        )
        assert "u1/n1/n1.md" not in manifest["files"]
        assert manifest["unstable"][0]["path"] == "u1/n1/n1.md"

    def test_io_error_is_reported_as_failed(self, tree, monkeypatch):
        """读不了的文件归入 failed（而不是 unstable），同样不能静默"""
        storage, backup = tree

        def simple(src: Path, dst: Path):
            if src.name == "n2.mp4":
                return False, "IO 错误: 模拟拒绝访问"
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
            return True, ss._sha256_file(src)

        monkeypatch.setattr(ss, "_copy_verified", simple)
        result = ss.create_storage_snapshot(
            storage_root=storage, backup_root=backup, keep=3
        )
        assert result["ok"] is False
        assert [f["path"] for f in result["failed"]] == ["u2/n2/n2.mp4"]
        assert result["unstable"] == []


# ===========================================================================
# 保留策略：只删自己的目录
# ===========================================================================

class TestRetention:
    def test_prunes_oldest_keeps_newest(self, tree):
        storage, backup = tree
        stamps = [
            datetime(2026, 9, 1, 4, 45), datetime(2026, 9, 5, 4, 45),
            datetime(2026, 9, 9, 4, 45), datetime(2026, 9, 12, 4, 45),
        ]
        for stamp in stamps:
            ss.create_storage_snapshot(
                storage_root=storage, backup_root=backup, keep=3, now=stamp
            )
        names = sorted(p.name for p in backup.iterdir() if p.is_dir())
        assert names == [
            "20260905-044500-storage",
            "20260909-044500-storage",
            "20260912-044500-storage",
        ]

    def test_never_touches_db_snapshots_or_manual_backups(self, tree):
        """★ 关键安全性质：数据库快照与手工备份**永远**不在删除范围内"""
        storage, backup = tree
        # 手工备份目录（早期只有 storage/.env）
        manual = backup / "20260911-3-6-pre-fsrs"
        manual.mkdir()
        (manual / "engramnote.db").write_bytes(b"sqlite")
        (manual / "storage").mkdir()
        (manual / "storage" / "keep.txt").write_text("must survive", encoding="utf-8")
        # 4 份存储快照 + keep=1 → 应只留 1 份，其余全删
        for day in (1, 5, 9, 12):
            ss.create_storage_snapshot(
                storage_root=storage, backup_root=backup, keep=10,
                now=datetime(2026, 9, day, 4, 45),
            )
        removed = ss.prune_storage_snapshots(1, backup_root=backup)

        assert len(removed) == 3
        assert manual.exists()
        assert (manual / "storage" / "keep.txt").read_text(encoding="utf-8") == "must survive"
        assert (manual / "engramnote.db").exists()
        assert sorted(p.name for p in backup.iterdir() if p.is_dir()) == [
            "20260911-3-6-pre-fsrs", "20260912-044500-storage",
        ]

    def test_keep_zero_is_a_noop(self, tree):
        """`keep=0` 不能变成"删光所有快照"的意外开关"""
        storage, backup = tree
        ss.create_storage_snapshot(
            storage_root=storage, backup_root=backup, keep=10,
            now=datetime(2026, 9, 12, 4, 45),
        )
        assert ss.prune_storage_snapshots(0, backup_root=backup) == []
        assert len(list(backup.iterdir())) == 1

    def test_prune_on_missing_backup_root(self, tmp_path):
        assert ss.prune_storage_snapshots(3, backup_root=tmp_path / "nope") == []


# ===========================================================================
# 检查（只读）
# ===========================================================================

class TestInspect:
    def _snapshot(self, storage, backup):
        return Path(ss.create_storage_snapshot(
            storage_root=storage, backup_root=backup, keep=3
        )["snapshot"])

    def test_intact_snapshot_passes(self, tree):
        storage, backup = tree
        result = ss.inspect_storage_snapshot(self._snapshot(storage, backup))
        assert result["ok"] is True
        assert result["problems"] == []
        assert result["counts"] == {
            "checked": 4, "missing": 0, "size_mismatch": 0, "hash_mismatch": 0,
        }
        assert result["extra"] == []

    def test_missing_file_is_detected(self, tree):
        storage, backup = tree
        snapshot = self._snapshot(storage, backup)
        (snapshot / "u1/n1/n1.md").unlink()

        result = ss.inspect_storage_snapshot(snapshot)
        assert result["ok"] is False
        assert result["counts"]["missing"] == 1
        assert [p["path"] for p in result["problems"]] == ["u1/n1/n1.md"]
        assert "不完整" in result["reason"]

    def test_size_mismatch_is_detected(self, tree):
        storage, backup = tree
        snapshot = self._snapshot(storage, backup)
        (snapshot / "u1/n1/n1.pdf").write_bytes(b"x")  # 大小变了

        result = ss.inspect_storage_snapshot(snapshot)
        assert result["ok"] is False
        assert result["counts"]["size_mismatch"] == 1
        problem = result["problems"][0]
        assert problem["issue"] == "size_mismatch"
        assert problem["expected"] == "13" and problem["actual"] == "1"

    def test_hash_mismatch_with_same_size_is_detected(self, tree):
        """★ 大小相同、内容被改写 —— 只有哈希能发现（这就是清单存 sha256 的理由）"""
        storage, backup = tree
        snapshot = self._snapshot(storage, backup)
        original = (snapshot / "u1/n1/n1.md").read_bytes()
        (snapshot / "u1/n1/n1.md").write_bytes(b"X" * len(original))

        result = ss.inspect_storage_snapshot(snapshot)
        assert result["ok"] is False
        assert result["counts"]["size_mismatch"] == 0
        assert result["counts"]["hash_mismatch"] == 1
        assert result["problems"][0]["issue"] == "hash_mismatch"

    def test_extra_file_is_information_not_a_problem(self, tree):
        storage, backup = tree
        snapshot = self._snapshot(storage, backup)
        (snapshot / "u3/later.pdf").parent.mkdir(parents=True, exist_ok=True)
        (snapshot / "u3/later.pdf").write_bytes(b"later")

        result = ss.inspect_storage_snapshot(snapshot)
        assert result["ok"] is True
        assert result["extra"] == ["u3/later.pdf"]

    def test_not_a_storage_snapshot(self, tmp_path):
        plain = tmp_path / "20260911-3-6-pre-fsrs"
        plain.mkdir()
        result = ss.inspect_storage_snapshot(plain)
        assert result["ok"] is False
        assert "不是存储快照" in result["reason"]

    def test_corrupt_manifest_is_reported(self, tree):
        storage, backup = tree
        snapshot = self._snapshot(storage, backup)
        (snapshot / ss.MANIFEST_FILENAME).write_text("{ 坏掉的 json", encoding="utf-8")
        result = ss.inspect_storage_snapshot(snapshot)
        assert result["ok"] is False
        assert "清单无法解析" in result["reason"]

    def test_inspect_is_read_only(self, tree):
        """检查绝不能修改快照：它是唯一的救命备份"""
        storage, backup = tree
        snapshot = self._snapshot(storage, backup)
        before = {
            p.relative_to(snapshot).as_posix(): p.read_bytes()
            for p in snapshot.rglob("*") if p.is_file()
        }
        ss.inspect_storage_snapshot(snapshot)
        after = {
            p.relative_to(snapshot).as_posix(): p.read_bytes()
            for p in snapshot.rglob("*") if p.is_file()
        }
        assert before == after


# ===========================================================================
# 恢复
# ===========================================================================

class TestRestore:
    def _snapshot(self, storage, backup):
        return Path(ss.create_storage_snapshot(
            storage_root=storage, backup_root=backup, keep=3
        )["snapshot"])

    def test_restores_deleted_files(self, tree, tmp_path):
        """主场景：文件被误删 → 从快照补回"""
        storage, backup = tree
        snapshot = self._snapshot(storage, backup)
        target = tmp_path / "live"
        _make_tree(target, {"u1/n1/n1.pdf": b"newer pdf"})  # 现存一个更新的文件

        result = ss.restore_storage_snapshot(snapshot, storage_root=target)
        assert result["ok"] is True
        assert sorted(result["restored"]) == [
            "u1/n1/n1.md", "u2/n2/n2.json", "u2/n2/n2.mp4",
        ]
        assert result["skipped"] == ["u1/n1/n1.pdf"]
        # 已存在的文件**没有被覆盖**（默认 overwrite=False）
        assert (target / "u1/n1/n1.pdf").read_bytes() == b"newer pdf"
        assert (target / "u1/n1/n1.md").read_bytes() == (storage / "u1/n1/n1.md").read_bytes()

    def test_overwrite_is_opt_in(self, tree, tmp_path):
        storage, backup = tree
        snapshot = self._snapshot(storage, backup)
        target = tmp_path / "live"
        _make_tree(target, {"u1/n1/n1.pdf": b"newer pdf"})

        result = ss.restore_storage_snapshot(
            snapshot, storage_root=target, overwrite=True
        )
        assert result["ok"] is True
        assert result["skipped"] == []
        assert (target / "u1/n1/n1.pdf").read_bytes() == (storage / "u1/n1/n1.pdf").read_bytes()

    def test_never_deletes_extra_files(self, tree, tmp_path):
        """★ 恢复不得让用户后来上传的内容消失"""
        storage, backup = tree
        snapshot = self._snapshot(storage, backup)
        target = tmp_path / "live"
        _make_tree(target, {"u9/n9/n9.pdf": b"uploaded after the snapshot"})

        result = ss.restore_storage_snapshot(snapshot, storage_root=target)
        assert result["ok"] is True
        assert (target / "u9/n9/n9.pdf").read_bytes() == b"uploaded after the snapshot"

    def test_refuses_to_restore_tampered_file(self, tree, tmp_path):
        """快照里的文件被改过（哈希不符）→ 不写进目标目录"""
        storage, backup = tree
        snapshot = self._snapshot(storage, backup)
        (snapshot / "u1/n1/n1.md").write_bytes(b"tampered!")
        target = tmp_path / "live"

        result = ss.restore_storage_snapshot(snapshot, storage_root=target)
        assert result["ok"] is False
        assert result["mismatched"] == ["u1/n1/n1.md"]
        assert not (target / "u1/n1/n1.md").exists()

    def test_reports_file_missing_from_snapshot_itself(self, tree, tmp_path):
        storage, backup = tree
        snapshot = self._snapshot(storage, backup)
        (snapshot / "u2/n2/n2.json").unlink()

        result = ss.restore_storage_snapshot(snapshot, storage_root=tmp_path / "live")
        assert result["ok"] is False
        assert result["problems"] == [{"path": "u2/n2/n2.json", "issue": "快照内缺失"}]

    def test_not_a_storage_snapshot(self, tmp_path):
        plain = tmp_path / "whatever"
        plain.mkdir()
        result = ss.restore_storage_snapshot(plain, storage_root=tmp_path / "live")
        assert result["ok"] is False
        assert "不是存储快照" in result["reason"]


# ===========================================================================
# 列表
# ===========================================================================

class TestList:
    def test_lists_newest_first_with_stats(self, tree):
        storage, backup = tree
        for day in (1, 9):
            ss.create_storage_snapshot(
                storage_root=storage, backup_root=backup, keep=10,
                now=datetime(2026, 9, day, 4, 45),
            )
        items = ss.list_storage_snapshots(backup_root=backup)
        assert [i["name"] for i in items] == [
            "20260909-044500-storage", "20260901-044500-storage",
        ]
        assert all(i["files"] == 4 for i in items)
        assert all(i["unstable"] == 0 and i["failed"] == 0 for i in items)

    def test_ignores_db_snapshot_dirs_and_plain_files(self, tree):
        storage, backup = tree
        db_dir = backup / "20260911-4-6-pre-prompt-version"
        db_dir.mkdir()
        (db_dir / "engramnote.db").write_bytes(b"sqlite")
        (backup / "loose.txt").write_text("x", encoding="utf-8")

        assert ss.list_storage_snapshots(backup_root=backup) == []

    def test_missing_backup_root_returns_empty(self, tmp_path):
        assert ss.list_storage_snapshots(backup_root=tmp_path / "nope") == []

    def test_corrupt_manifest_is_flagged(self, tree):
        storage, backup = tree
        ss.create_storage_snapshot(
            storage_root=storage, backup_root=backup, keep=3,
            now=datetime(2026, 9, 12, 4, 45),
        )
        (backup / "20260912-044500-storage" / ss.MANIFEST_FILENAME).write_text(
            "not json", encoding="utf-8"
        )
        items = ss.list_storage_snapshots(backup_root=backup)
        assert len(items) == 1
        assert "清单损坏" in items[0]["manifest_error"]


# ===========================================================================
# 端到端：删文件 → 有快照 → 能补回来
# ===========================================================================

def test_end_to_end_delete_then_restore(tree, tmp_path):
    """把"文件被误删"这条真实路径走一遍（含检查 → 补回 → 复查）"""
    storage, backup = tree
    created = ss.create_storage_snapshot(
        storage_root=storage, backup_root=backup, keep=3,
        now=datetime(2026, 9, 12, 4, 45),
    )
    snapshot = Path(created["snapshot"])
    assert ss.inspect_storage_snapshot(snapshot)["ok"] is True

    # 误删两个文件
    (storage / "u1/n1/n1.md").unlink()
    (storage / "u2/n2/n2.mp4").unlink()

    restore = ss.restore_storage_snapshot(snapshot, storage_root=storage)
    assert restore["ok"] is True
    assert sorted(restore["restored"]) == ["u1/n1/n1.md", "u2/n2/n2.mp4"]
    # 内容与快照完全一致
    for rel in ("u1/n1/n1.md", "u2/n2/n2.mp4"):
        assert (storage / rel).read_bytes() == (snapshot / rel).read_bytes()
    # 补回后重新做一份快照仍是完好的
    again = ss.create_storage_snapshot(
        storage_root=storage, backup_root=backup, keep=3,
        now=datetime(2026, 9, 19, 4, 45),
    )
    assert again["ok"] is True
    assert ss.inspect_storage_snapshot(Path(again["snapshot"]))["ok"] is True
