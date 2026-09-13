"""对象存储（`data/storage/`）快照 —— 补上"数据库能恢复、文件不能"的那一半

## 为什么需要它

数据库快照（`backup_service.create_snapshot`）用 `VACUUM INTO` 做成**单文件原子**
副本，`data/db/engramnote.db` 因此随时可以回到过去某一刻。

但用户真正看到的内容分布在**两个**地方：

    data/db/engramnote.db   元数据（笔记、卡片、题目、复习进度、文件路径）
    data/storage/<user>/<note>/…  原始上传（pdf/mp4）与派生产物（md/json）

只恢复数据库而文件丢了，用户看到的是"笔记都在，但每一篇都打不开" —— 与丢数据
没有区别。而文件**不能**用 `VACUUM INTO` 那种方式原子化，所以必须单独处理，
并且必须诚实地说明它和数据库快照的**强度差别**（见下）。

## 与数据库快照的强度差别（重要，不要混淆）

| | 数据库快照 | 存储快照 |
|---|---|---|
| 原子性 | **原子**（`VACUUM INTO` + 一致性读） | **非原子**：逐文件复制，复制期间的上传不会被包含 |
| 一致性判据 | `integrity_check` + 行数 | 每个文件的 sha256 **复制前后比对** |
| 结论 | "这份快照等于某一刻的库" | "这份快照里每个文件都是完整的；某一刻之后新增的文件可能不在里面" |

非原子不等于不可用：恢复"被误删的旧文件"这件事，快照只要**文件完整**就够了。
关键是不能假装它是原子的 —— 因此：

- 逐文件 **边复制边算 sha256**，复制完再读回目标文件算一次，两者不符就**重试一次**，
  仍不符的文件**不进清单**（宁可不恢复，也不恢复一个撕裂的文件）；
- 这类文件与本次跳过的文件都写进清单的 `unstable` / `failed` 字段，**报告出来而不是吞掉**。

## 清单（manifest）是快照的身份证

`_storage_manifest.json` 记录每个文件的相对路径、大小、mtime、sha256，以及本次
快照的统计与异常。`inspect_storage_snapshot` 只依赖它就能判断快照是否仍然完好，
`restore_storage_snapshot` 只依赖它就能知道该补哪些文件 —— 不需要目录遍历时
再去猜"这个目录是不是我们的快照"。

## 删除策略：只删自己写的目录

保留策略**只考虑含 `_storage_manifest.json` 的目录**，且删除走 `shutil.rmtree`
之前还要再确认一次该文件存在。备份目录（`_backup/`）里同时还放着数据库快照与
早期手工备份，误删一份就是不可逆的损失 —— 这里宁可少删，绝不多删。
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..config import PROJECT_ROOT

logger = logging.getLogger(__name__)

#: 线上对象存储目录
STORAGE_ROOT = PROJECT_ROOT / "data" / "storage"

#: 备份根目录（与 `backup_service.BACKUP_ROOT` 保持一致）
BACKUP_ROOT = PROJECT_ROOT.parent / "_backup"

#: 清单文件名：既是快照的身份证，也是保留策略的识别标志
MANIFEST_FILENAME = "_storage_manifest.json"

#: 快照目录名后缀，用于和数据库快照（`<stamp>-<label>`）区分
SNAPSHOT_SUFFIX = "storage"

#: 复制缓冲区大小（64 KiB）
_CHUNK = 64 * 1024


def default_storage_retention() -> int:
    """默认保留份数（可用配置 `storage_backup_keep` 覆盖）"""
    from ..config import get_settings

    return getattr(get_settings(), "storage_backup_keep", 3)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(_CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _copy_verified(src: Path, dst: Path) -> tuple[bool, str]:
    """复制单个文件并校验完整性；返回 `(成功, sha256 或失败原因)`

    边读边写边算哈希，写完再读回目标文件算一次：两者一致才认为这份副本可用。
    不一致时重试一次（大概率是复制期间源文件被改写），仍不一致就放弃。
    """
    for _ in range(2):
        digest = hashlib.sha256()
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            with src.open("rb") as fin, dst.open("wb") as fout:
                while True:
                    block = fin.read(_CHUNK)
                    if not block:
                        break
                    digest.update(block)
                    fout.write(block)
            expected = digest.hexdigest()
            if _sha256_file(dst) == expected:
                return True, expected
        except OSError as exc:
            return False, f"IO 错误: {exc}"
    return False, "复制后哈希不一致（源文件在复制期间被改写或磁盘异常）"


def _iter_storage_files(root: Path):
    """按相对 POSIX 路径稳定排序地遍历文件（排序保证清单可复现）"""
    entries = [p for p in root.rglob("*") if p.is_file()]
    entries.sort(key=lambda p: p.relative_to(root).as_posix())
    return entries


def create_storage_snapshot(
    *,
    storage_root: Optional[Path] = None,
    backup_root: Optional[Path] = None,
    keep: Optional[int] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """把整个 `data/storage/` 复制进备份目录，返回本次快照的报告

    Args:
        storage_root: 源目录；缺省 `data/storage/`
        backup_root: 备份根目录；缺省 `_backup/`
        keep: 保留份数；缺省读配置（默认 3）
        now: 时间戳来源（测试可注入，保证可复现）

    Returns:
        `{"ok", "snapshot", "files", "bytes", "unstable", "failed", "pruned", "reason"}`

    源目录不存在**不算失败**：全新部署还没上传过任何文件时就是这样，
    此时返回 `ok=True` 且 `files=0`，只在日志里说明。
    """
    source = Path(storage_root) if storage_root else STORAGE_ROOT
    backup = Path(backup_root) if backup_root else BACKUP_ROOT
    keep_count = default_storage_retention() if keep is None else keep

    result: dict[str, Any] = {
        "ok": False, "snapshot": None, "files": 0, "bytes": 0,
        "unstable": [], "failed": [], "pruned": [], "reason": None,
    }

    if not source.exists():
        result["ok"] = True
        result["reason"] = f"存储目录不存在，跳过（{source}）"
        logger.info("存储快照跳过：%s", result["reason"])
        return result

    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    target_dir = backup / f"{stamp}-{SNAPSHOT_SUFFIX}"
    if target_dir.exists():
        result["reason"] = f"目标已存在，换一个时间戳: {target_dir}"
        logger.error("存储快照失败：%s", result["reason"])
        return result

    target_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "created_at": stamp,
        "kind": "storage",
        "source": str(source),
        "files": {},
    }

    total_bytes = 0
    unstable: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []

    for src in _iter_storage_files(source):
        rel = src.relative_to(source).as_posix()
        try:
            stat = src.stat()
        except OSError as exc:
            failed.append({"path": rel, "error": f"读取属性失败: {exc}"})
            continue
        ok, value = _copy_verified(src, target_dir / rel)
        if ok:
            manifest["files"][rel] = {
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "sha256": value,
            }
            total_bytes += stat.st_size
        elif value.startswith("复制后哈希不一致"):
            unstable.append({"path": rel, "error": value})
        else:
            failed.append({"path": rel, "error": value})

    manifest["files_count"] = len(manifest["files"])
    manifest["bytes"] = total_bytes
    manifest["unstable"] = unstable
    manifest["failed"] = failed
    (target_dir / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    result["snapshot"] = str(target_dir)
    result["files"] = len(manifest["files"])
    result["bytes"] = total_bytes
    result["unstable"] = unstable
    result["failed"] = failed
    result["pruned"] = prune_storage_snapshots(keep_count, backup_root=backup)
    # 有文件没能完整复制 = 这份快照是**不完整**的，必须让调用方看见
    result["ok"] = not failed and not unstable

    level = logger.info if result["ok"] else logger.warning
    level(
        "存储快照完成: %s | %d 个文件 / %.1f MB | 不稳定=%d 失败=%d | 清理旧快照=%s",
        target_dir, result["files"], total_bytes / 1024 / 1024,
        len(unstable), len(failed), result["pruned"] or "无",
    )
    return result


def prune_storage_snapshots(
    keep: int, *, backup_root: Optional[Path] = None
) -> list[str]:
    """只保留最近 N 份**存储快照**，返回被删除的目录名

    识别标志是目录里有 `_storage_manifest.json` —— 数据库快照目录没有这个文件，
    早期手工备份也没有，因此它们**永远不会**被这个函数碰到。
    """
    backup = Path(backup_root) if backup_root else BACKUP_ROOT
    if keep <= 0 or not backup.exists():
        return []

    candidates = [
        d for d in backup.iterdir()
        if d.is_dir() and (d / MANIFEST_FILENAME).exists()
    ]
    candidates.sort(key=lambda d: d.name, reverse=True)

    removed: list[str] = []
    for directory in candidates[keep:]:
        # 双重确认：进入删除路径前再检查一次身份标志，避免目录被并发替换
        if not (directory / MANIFEST_FILENAME).exists():
            continue
        shutil.rmtree(directory, ignore_errors=True)
        removed.append(directory.name)
    return removed


def is_storage_snapshot(directory: Path) -> bool:
    """目录是否为存储快照（供列表与恢复入口做参数校验）"""
    return (Path(directory) / MANIFEST_FILENAME).exists()


def list_storage_snapshots(
    *, backup_root: Optional[Path] = None
) -> list[dict[str, Any]]:
    """列出全部存储快照（按名称倒序 = 时间倒序），缺清单的目录会被标出"""
    backup = Path(backup_root) if backup_root else BACKUP_ROOT
    if not backup.exists():
        return []

    items: list[dict[str, Any]] = []
    for directory in sorted(backup.iterdir(), reverse=True):
        if not directory.is_dir() or not (directory / MANIFEST_FILENAME).exists():
            continue
        record: dict[str, Any] = {"name": directory.name, "path": str(directory)}
        try:
            manifest = json.loads(
                (directory / MANIFEST_FILENAME).read_text(encoding="utf-8")
            )
            record.update({
                "created_at": manifest.get("created_at"),
                "files": manifest.get("files_count"),
                "bytes": manifest.get("bytes"),
                "unstable": len(manifest.get("unstable") or []),
                "failed": len(manifest.get("failed") or []),
            })
        except (json.JSONDecodeError, OSError) as exc:
            record["manifest_error"] = f"清单损坏: {exc}"
        items.append(record)
    return items


def inspect_storage_snapshot(snapshot_dir: Path) -> dict[str, Any]:
    """核对快照里每个文件是否仍然与清单一致（只读，不修改快照）

    判据（与 `backup_service.inspect_snapshot` 的取向一致）：

    - `missing`      清单里有、快照里没有 → **问题**（快照本身残缺）
    - `size_mismatch` 大小不符          → **问题**
    - `hash_mismatch` 大小对但哈希不符   → **问题**（内容被改写/静默损坏）
    - `extra`        快照里有、清单里没有 → **信息**（例如清单写完后残留的副本）

    返回 `{"ok", "problems", "counts", "reason"}`；`ok` 为假只表示"这份快照不可信"。
    """
    directory = Path(snapshot_dir)
    result: dict[str, Any] = {
        "ok": False, "snapshot": str(directory), "reason": None,
        "problems": [], "counts": {},
    }

    manifest_file = directory / MANIFEST_FILENAME
    if not manifest_file.exists():
        result["reason"] = f"不是存储快照（缺 {MANIFEST_FILENAME}）: {directory}"
        return result
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        result["reason"] = f"清单无法解析: {exc}"
        return result

    files = manifest.get("files") or {}
    problems: list[dict[str, str]] = []
    counts = {"checked": 0, "missing": 0, "size_mismatch": 0, "hash_mismatch": 0}

    for rel, meta in files.items():
        counts["checked"] += 1
        path = directory / rel
        if not path.exists():
            counts["missing"] += 1
            problems.append({"path": rel, "issue": "missing"})
            continue
        actual_size = path.stat().st_size
        if actual_size != meta.get("size"):
            counts["size_mismatch"] += 1
            problems.append({
                "path": rel, "issue": "size_mismatch",
                "expected": str(meta.get("size")), "actual": str(actual_size),
            })
            continue
        if meta.get("sha256") and _sha256_file(path) != meta["sha256"]:
            counts["hash_mismatch"] += 1
            problems.append({"path": rel, "issue": "hash_mismatch"})

    # 清单之外多出来的文件只是信息：清单写完后残留的副本不影响恢复能力
    known = set(files)
    extra = [
        p.relative_to(directory).as_posix()
        for p in _iter_storage_files(directory)
        if p.name != MANIFEST_FILENAME
        and p.relative_to(directory).as_posix() not in known
    ]

    result["problems"] = problems
    result["counts"] = counts
    result["extra"] = sorted(extra)
    # 快照自身的不稳定/失败记录也要带上：它决定了这份快照"天生"缺了什么
    result["unstable"] = manifest.get("unstable") or []
    result["failed"] = manifest.get("failed") or []
    result["ok"] = not problems
    if not result["ok"]:
        result["reason"] = (
            f"快照不完整: 缺失 {counts['missing']} / 大小不符 {counts['size_mismatch']}"
            f" / 哈希不符 {counts['hash_mismatch']}"
        )
    return result


def restore_storage_snapshot(
    snapshot_dir: Path,
    *,
    storage_root: Optional[Path] = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """把快照里的文件补回存储目录（默认**只补缺失的文件**）

    这是**破坏性操作**里最保守的一种：

    - 默认 `overwrite=False`：已存在的文件一律不动。理由是恢复场景里最常见的是
      "某几个文件被误删"，而现存文件通常比快照里的新；
    - **绝不删除**目标目录里多出来的文件（用户后来上传的内容不能因为恢复而消失）；
    - 每个补回的文件都按清单里的 sha256 校验一次，不符就不写进去。

    Args:
        snapshot_dir: 快照目录
        storage_root: 目标目录；缺省 `data/storage/`
        overwrite: 是否覆盖同名文件（需要显式开启）

    Returns:
        `{"ok", "restored", "skipped", "mismatched", "problems", "reason"}`
    """
    directory = Path(snapshot_dir)
    target_root = Path(storage_root) if storage_root else STORAGE_ROOT
    result: dict[str, Any] = {
        "ok": False, "snapshot": str(directory), "target": str(target_root),
        "restored": [], "skipped": [], "mismatched": [], "problems": [],
        "reason": None,
    }

    manifest_file = directory / MANIFEST_FILENAME
    if not manifest_file.exists():
        result["reason"] = f"不是存储快照（缺 {MANIFEST_FILENAME}）: {directory}"
        return result
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        result["reason"] = f"清单无法解析: {exc}"
        return result

    for rel, meta in (manifest.get("files") or {}).items():
        dest = target_root / rel
        if dest.exists() and not overwrite:
            result["skipped"].append(rel)
            continue
        source = directory / rel
        if not source.exists():
            result["problems"].append({"path": rel, "issue": "快照内缺失"})
            continue
        if meta.get("sha256") and _sha256_file(source) != meta["sha256"]:
            result["mismatched"].append(rel)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        result["restored"].append(rel)

    result["ok"] = not result["problems"] and not result["mismatched"]
    if result["restored"]:
        logger.info(
            "存储恢复: 补回 %d 个文件到 %s（跳过已存在 %d 个）",
            len(result["restored"]), target_root, len(result["skipped"]),
        )
    return result


__all__ = [
    "STORAGE_ROOT",
    "MANIFEST_FILENAME",
    "create_storage_snapshot",
    "prune_storage_snapshots",
    "is_storage_snapshot",
    "list_storage_snapshots",
    "inspect_storage_snapshot",
    "restore_storage_snapshot",
    "default_storage_retention",
]
