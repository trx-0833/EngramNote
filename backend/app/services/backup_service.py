"""
数据库备份服务

用 `VACUUM INTO` 做**原子快照**，并提供完整性校验与保留策略。

## 为什么用 VACUUM INTO 而不是拷贝文件

SQLite 开了 WAL：数据库内容分散在 `engramnote.db` + `-wal` + `-shm` 三个
文件里，直接复制主库文件会得到一个**缺了最近事务**的快照 —— 看起来备份
成功，恢复时才发现少了数据。`VACUUM INTO` 由 SQLite 自己保证一致性，
产出单个自包含文件，且不需要停机。

## 为什么快照后立刻校验

备份的价值全部取决于"能不能恢复"。一个静默损坏的快照比没有备份更危险
（人以为有备份）。因此每份快照落盘后立即只读打开、跑 `integrity_check`、
统计关键表行数，并把结果记录在 `_verify.json` 里。恢复前不看这份记录
就恢复，等于赌运气。
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..config import DB_DIR, PROJECT_ROOT

logger = logging.getLogger(__name__)

#: 备份根目录
#:
#: 刻意放在**仓库根**（`PROJECT_ROOT` 的上一级，即与 `backend/` 平级），
#: 而不是 `backend/data/` 之下：
#:   - 备份与"被备份的数据"放在同一目录树里，一次误删 data/ 就同时失去两者；
#:   - `data/` 已被 .gitignore 忽略，备份放在仓库根更容易被人看见。
#: 注意 `config.PROJECT_ROOT` 实际指向 `backend/`，所以要再上一级。
BACKUP_ROOT = PROJECT_ROOT.parent / "_backup"

#: 默认数据库文件
DEFAULT_DB = DB_DIR / "engramnote.db"

#: 需要统计行数的关键业务表（缺表不报错，只记 None）
KEY_TABLES = ("users", "notes", "knowledge_cards", "quiz_items", "review_logs")

#: 快照中存放校验信息的文件名
VERIFY_FILENAME = "_verify.json"


def resolve_db_path(explicit: Optional[str] = None) -> Path:
    """定位要备份的 SQLite 文件"""
    if explicit:
        return Path(explicit).resolve()
    return DEFAULT_DB


def verify_snapshot(db_file: Path) -> dict[str, Any]:
    """在快照上做完整性校验与行数统计（只读打开，绝不修改快照）"""
    con = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        counts: dict[str, Optional[int]] = {}
        for table in KEY_TABLES:
            try:
                counts[table] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                counts[table] = None
        return {"integrity": integrity, "counts": counts}
    finally:
        con.close()


def create_snapshot(
    db_file: Path,
    label: Optional[str] = None,
    *,
    now: Optional[datetime] = None,
) -> Path:
    """用 `VACUUM INTO` 创建原子快照，返回快照路径

    Args:
        db_file: 源数据库文件
        label: 快照标签（便于识别，如 pre-migration / daily）
        now: 时间戳来源（测试可注入，保证可复现）

    Raises:
        FileNotFoundError: 源数据库不存在
        FileExistsError: 同一秒内同名快照已存在
    """
    if not db_file.exists():
        raise FileNotFoundError(f"数据库不存在: {db_file}")

    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    name = f"{stamp}-{label}" if label else stamp
    target_dir = BACKUP_ROOT / name
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "engramnote.db"

    if target.exists():
        raise FileExistsError(f"目标已存在，换一个 label: {target}")

    # VACUUM INTO 要求目标文件**不存在**，且路径中的单引号需转义
    escaped = str(target).replace("'", "''")
    source = sqlite3.connect(str(db_file))
    try:
        source.execute(f"VACUUM INTO '{escaped}'")
    finally:
        source.close()

    # 立刻校验并把结果写进快照目录：恢复时无需猜测这份备份是否完好
    info = verify_snapshot(target)
    (target_dir / VERIFY_FILENAME).write_text(
        json.dumps(
            {
                "created_at": stamp,
                "label": label,
                "source": str(db_file),
                "snapshot": str(target),
                "size_bytes": target.stat().st_size,
                **info,
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    logger.info(
        "数据库快照完成: %s | integrity=%s | %s",
        target, info["integrity"],
        " ".join(f"{k}={v}" for k, v in info["counts"].items()),
    )
    return target


def prune_snapshots(keep: int) -> list[str]:
    """只保留最近 N 份**含 db 快照**的备份目录，返回被删除的目录名

    只数含 `engramnote.db` 的目录：早期的手工备份目录（只有 storage/.env）
    不应占用保留名额，否则会把真正可恢复的快照挤掉。
    """
    if keep <= 0 or not BACKUP_ROOT.exists():
        return []

    candidates = [
        d for d in BACKUP_ROOT.iterdir()
        if d.is_dir() and (d / "engramnote.db").exists()
    ]
    candidates.sort(key=lambda d: d.name, reverse=True)

    removed: list[str] = []
    for directory in candidates[keep:]:
        for child in directory.rglob("*"):
            if child.is_file():
                child.unlink()
        # 自底向上删空目录
        for child in sorted(directory.rglob("*"), reverse=True):
            if child.is_dir():
                child.rmdir()
        directory.rmdir()
        removed.append(directory.name)
    return removed


def list_snapshots() -> list[dict[str, Any]]:
    """列出全部备份目录及其校验信息（供 --list 与运维查看）"""
    if not BACKUP_ROOT.exists():
        return []

    items: list[dict[str, Any]] = []
    for directory in sorted(BACKUP_ROOT.iterdir(), reverse=True):
        if not directory.is_dir():
            continue
        db_file = directory / "engramnote.db"
        record: dict[str, Any] = {
            "name": directory.name,
            "has_db": db_file.exists(),
            "size_bytes": db_file.stat().st_size if db_file.exists() else None,
        }
        verify_file = directory / VERIFY_FILENAME
        if verify_file.exists():
            try:
                record.update(json.loads(verify_file.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                record["verify_error"] = "校验文件损坏"
        elif db_file.exists():
            # 没有校验记录的旧快照：现场补一次，避免"未知状态"长期存在
            try:
                record.update(verify_snapshot(db_file))
            except sqlite3.Error as exc:
                record["verify_error"] = str(exc)
        items.append(record)
    return items


def default_retention() -> int:
    """默认保留份数（可用配置覆盖）"""
    from ..config import get_settings

    return getattr(get_settings(), "backup_keep", 14)


def run_scheduled_backup(label: str = "daily") -> dict[str, Any]:
    """执行一次定时备份（含保留策略），返回结果摘要

    刻意**不抛异常**给调用方（Celery Beat 任务）：备份失败必须被记录并
    可观测，但不应该让 Beat 任务堆失败状态。调用方据返回值决定告警。
    """
    result: dict[str, Any] = {"ok": False, "label": label}
    try:
        db_file = resolve_db_path()
        if not db_file.exists():
            result["error"] = f"数据库不存在: {db_file}"
            logger.error("定时备份跳过：%s", result["error"])
            return result

        target = create_snapshot(db_file, label=label)
        info = verify_snapshot(target)
        result.update({
            "ok": info["integrity"] == "ok",
            "snapshot": str(target),
            "integrity": info["integrity"],
            "counts": info["counts"],
            "size_bytes": target.stat().st_size,
        })

        keep = default_retention()
        if keep > 0:
            result["pruned"] = prune_snapshots(keep)

        # 完整性不是 ok 时用 error 级别：静默损坏的备份比没有备份更危险
        if info["integrity"] != "ok":
            logger.error("定时备份完整性校验失败: %s -> %s", target, info["integrity"])
        return result
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        logger.error("定时备份失败: %s", result["error"], exc_info=True)
        return result


__all__ = [
    "BACKUP_ROOT", "DEFAULT_DB",
    "create_snapshot", "verify_snapshot", "prune_snapshots",
    "list_snapshots", "resolve_db_path", "run_scheduled_backup",
]
