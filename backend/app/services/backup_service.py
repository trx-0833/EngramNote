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
import shutil
import sqlite3
import tempfile
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
            "path": str(directory),
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


# ---------------------------------------------------------------------------
# 恢复演练（阶段 6.6：备份存在 ≠ 备份可用）
# ---------------------------------------------------------------------------

#: 演练时要核对的**深层一致性**（`verify_snapshot` 只做完整性 + 行数，不够）
#:
#: 两件事都写成小函数而不是 SQL 字符串，因为它们**不是查询**：
#: FTS5 的 `integrity-check` 是一条命令，失败时靠抛异常表达。
def _check_fts_consistency(con: sqlite3.Connection) -> Optional[str]:
    """FTS 索引的**结构**是否完好（FTS5 `integrity-check`）

    ## ⚠️ 一段被推翻的假设（写下来以免有人再犯）

    最初这里用 `fts5vocab` 数"索引里真正可检索的文档数"，再与 `chunks.grams`
    非空行数比对。真库上它报出：

        FTS 索引与 chunks.grams 不一致（可检索 171 行，应可检索 608 行）

    —— 看起来像"437 个块检索不到"的重大缺陷。**但这个结论是错的**，本轮实测：

    | 现象 | 实测结果 |
    |---|---|
    | `fts5vocab` 报告的文档数 | 171（在一致副本上新建一张同样的 FTS 表灌入同样数据，**也是 171**）|
    | `MATCH` 查 `chunk_rowid=600` 的词（`柴油`）| **命中 116 条** —— 它明明"不在索引里" |
    | `chunk_rowid=300/552/600` | `vocab` 说"无"，`MATCH` 全都能命中 |
    | `chunk_rowid=608`（grams 全是 `##`）| 0 条命中 —— 这一条是真的没有可索引词元 |

    也就是说：**`fts5vocab` 的 `doc` 不是一份完整普查**，据此外推会得到一个
    假警报。而假警报比没有检查更糟 —— 它会让演练永远红着，然后被所有人忽略。

    另外两条曾试过、同样不可行的判据（都实测过）：

    - `SELECT COUNT(*) FROM chunks_fts`：外部内容表会**直接读内容表**，永远相等；
    - `INSERT INTO chunks_fts(chunks_fts) VALUES('integrity-check')` 对外部内容表
      **不比对内容表**，索引少了行也不报错（但能发现索引内部结构损坏）。

    因此这里只做**能站得住**的那一条：FTS5 的 `integrity-check`（结构完整性）。
    它发现不了"索引落后于内容表"，但也不会误报 —— 而"索引落后"这件事，
    真实的判据是**检索结果**（见 `scripts/` 里的检索评估脚本），不是元数据统计。
    """
    try:
        con.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('integrity-check')")
    except sqlite3.Error as exc:
        if "no such" in str(exc).lower():
            return None   # 老快照没有 FTS 表 → 跳过
        return f"FTS 索引结构损坏: {exc}"
    return None


def _check_orphan_review_states(con: sqlite3.Connection) -> Optional[str]:
    """是否存在指向已删除卡片的复习进度（孤儿行）

    这类行会让复习队列对着空气调度：用户看到"今天要复习 5 张"，
    实际只有 4 张能打开。
    """
    try:
        orphans = con.execute(
            "SELECT COUNT(*) FROM review_states rs "
            "WHERE rs.item_type = 'card' AND NOT EXISTS "
            "(SELECT 1 FROM knowledge_cards kc WHERE kc.id = rs.item_id)"
        ).fetchone()[0]
    except sqlite3.Error:
        return None   # 表不存在 → 老快照，跳过
    if orphans:
        return f"存在 {orphans} 条指向已删除卡片的复习进度（孤儿行）"
    return None


def inspect_snapshot(db_file: Path, *, allow_write: bool = False) -> dict[str, Any]:
    """对快照做**深层**一致性检查

    比 `verify_snapshot` 多三件事：
      1. `PRAGMA foreign_key_check`（外键是否被破坏）；
      2. 深层一致性（复习进度有无孤儿；`allow_write=True` 时还包括 FTS 索引）；
      3. **真的把它当数据库用一次**（执行一次跨表查询）——
         结构完好但打不开的备份是存在的，而只有真查一次才知道。

    Args:
        db_file: 要检查的文件
        allow_write: 是否允许写这条连接。默认 False（**只读打开，绝不修改快照**）。
            FTS5 的 `integrity-check` 是一条写命令，因此只有在对
            **快照的临时副本**上检查时才传 True —— 见 `run_restore_drill`。

    Returns:
        `{"ok": bool, "problems": [...], "warnings": [...],
          "integrity": str, "counts": {...}}`
    """
    problems: list[str] = []
    warnings: list[str] = []
    target = str(db_file) if allow_write else f"file:{db_file}?mode=ro"
    con = sqlite3.connect(target, uri=not allow_write)
    try:
        try:
            integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        except sqlite3.DatabaseError as exc:
            # ⚠️ 严重损坏时 `integrity_check` **自己**会抛
            # `database disk image is malformed`，而不是返回一个字符串。
            # 这正是演练最该抓到的情形，因此在这里接住并直接判定失败 ——
            # 让它抛出去会让演练任务以异常结束，而"备份坏了"应当是一条
            # 可读的结论，不是一个 traceback。
            return {
                "ok": False,
                "problems": [f"快照无法读取（文件已损坏）: {exc}"],
                "warnings": warnings,
                "integrity": "unreadable",
                "counts": dict.fromkeys(KEY_TABLES),
            }

        if integrity != "ok":
            problems.append(f"integrity_check = {integrity}")

        # 1. 外键 —— 记为 **warning** 而不是 failure
        #
        # ⚠️ 本项目**有意保留悬挂引用**（见知识链接的"悬挂链接占位"策略与
        # `database.py::_rebuild_dangling_tables` 的说明）：某一行指向已被
        # 彻底删除的笔记/资料是设计的一部分。真库实测就有 1 处
        # （`note_material_links` 指向已删除的用户）。
        #
        # 把"任何外键违规"当失败，演练会**永远红着**，而永远红的演练等于没有 ——
        # 那正是演练最常见的死法。因此这里只报告数量，由人判断；
        # 硬失败留给"数据库本身不可用/索引与内容脱节"这类客观损坏。
        try:
            violations = con.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                warnings.append(
                    f"外键违规 {len(violations)} 处（悬挂引用策略下可能有意的，需人工确认）"
                )
        except sqlite3.Error as exc:
            warnings.append(f"外键检查失败: {exc}")

        # 2. 深层一致性（表不存在时由各自的检查函数跳过 —— 老快照可能还没这些表）
        checks = [_check_orphan_review_states]
        if allow_write:
            # FTS5 的 integrity-check 是写命令，只在对副本检查时才做
            checks.append(_check_fts_consistency)
        for check in checks:
            problem = check(con)
            if problem:
                problems.append(problem)

        # 3. 真的查一次（跨表 JOIN）：验证这份文件不只能过校验，还能被使用
        try:
            con.execute(
                "SELECT kc.id, kc.title FROM knowledge_cards kc "
                "JOIN users u ON u.id = kc.user_id LIMIT 1"
            ).fetchall()
        except sqlite3.Error as exc:
            problems.append(f"实际查询失败（备份可能不可用）: {exc}")

        counts: dict[str, Optional[int]] = {}
        for table in KEY_TABLES:
            try:
                counts[table] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                counts[table] = None
                problems.append(f"关键表缺失: {table}")
    finally:
        con.close()

    return {
        "ok": not problems,
        "problems": problems,
        "warnings": warnings,
        "integrity": integrity,
        "counts": counts,
    }


def run_restore_drill(
    snapshot: Optional[Path] = None,
    *,
    live_db: Optional[Path] = None,
) -> dict[str, Any]:
    """恢复演练：证明最近一份快照**真的能恢复**，且不动线上库

    ## 为什么需要它

    `create_snapshot` 已经会校验新快照。但那证明的是"**刚写下**的这一份完好"，
    而不能回答两个更关键的问题：

    1. 快照在**放置一段时间之后**还能用吗（磁盘损坏、被截断、被误改）；
    2. 快照里的数据与线上库的差距**是合理的吗**（差距过大说明备份停了）。

    因此演练要在最新快照上再跑一次完整检查，并把与线上库的**行数差异**报出来。

    ## 与线上库行数不同是**正常的**，不是失败

    快照是过去某一刻的状态，之后的新笔记、新卡片不会出现在里面。
    把这当成失败，演练就会天天"失败"，很快没人看它 —— 那是演练最常见的死法。
    因此这里只把差异作为**信息**返回，失败条件是
    "快照本身不可用/不一致"（见 `inspect_snapshot`）。

    ## 只读：绝不修改快照，也绝不碰线上库

    演练不做恢复动作本身（恢复是破坏性的，要人来做，见 `scripts/restore_db.py`）。
    线上库只用**只读**方式打开来数行数。

    Args:
        snapshot: 指定快照；缺省取 `list_snapshots()` 里最新的一份
        live_db: 线上库路径；缺省取 `resolve_db_path()`

    Returns:
        `{"ok": bool, "problems": [...], "reason": str|None, "snapshot": str|None,
          "counts": {...}, "live_counts": {...}, "deltas": {...}}`
    """
    result: dict[str, Any] = {
        "ok": False, "snapshot": None, "reason": None,
        "problems": [], "warnings": [], "counts": {}, "live_counts": {}, "deltas": {},
    }

    if snapshot is None:
        candidates = [i for i in list_snapshots() if i.get("has_db")]
        if not candidates:
            result["reason"] = "没有任何含 db 的快照可供演练"
            logger.error("恢复演练失败：%s", result["reason"])
            return result
        # `list_snapshots` 已按名称倒序（名字前缀是时间戳），取第一个即最新
        snapshot = Path(candidates[0]["path"]) / "engramnote.db"

    if not snapshot.exists():
        result["reason"] = f"快照不存在: {snapshot}"
        logger.error("恢复演练失败：%s", result["reason"])
        return result

    result["snapshot"] = str(snapshot)

    # ★ 演练 = **真的恢复一次**：复制到临时文件、在那里打开（可写）、查完删掉。
    #
    # 为什么不直接在快照上查：
    #   - 快照必须保持只读（它是唯一的救命备份，演练绝不能碰它）；
    #   - 而 FTS5 的 `integrity-check` 是写命令，只读连接上会报
    #     "attempt to write a readonly database"（本轮实测踩到）。
    # 副本上做检查同时满足两件事，而且"复制 + 打开 + 查询"本身就是恢复的
    # 最小可验证形态 —— 比只读打开更接近真实恢复。
    temp_dir = Path(tempfile.mkdtemp(prefix="restore-drill-"))
    temp_db = temp_dir / "snapshot-copy.db"
    try:
        shutil.copy2(snapshot, temp_db)
        inspection = inspect_snapshot(temp_db, allow_write=True)
    except OSError as exc:
        result["reason"] = f"无法复制快照做演练: {exc}"
        logger.error("恢复演练失败：%s", result["reason"])
        return result
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    result["problems"] = inspection["problems"]
    result["warnings"] = inspection.get("warnings", [])
    result["counts"] = inspection["counts"]

    live = live_db or resolve_db_path()
    if live.exists():
        try:
            live_info = verify_snapshot(live)  # 只读
            result["live_counts"] = live_info["counts"]
            result["deltas"] = {
                table: (inspection["counts"].get(table) or 0) - (live_info["counts"].get(table) or 0)
                for table in KEY_TABLES
            }
        except Exception as exc:  # noqa: BLE001 - 线上库读不到不该让演练"失败"
            result["problems"].append(f"线上库对比失败（忽略）: {exc}")

    result["ok"] = inspection["ok"]
    if result["ok"]:
        logger.info(
            "恢复演练通过: %s | 行数差异（快照-线上）: %s",
            snapshot.name,
            " ".join(f"{k}={v}" for k, v in result["deltas"].items()) or "无线上库可对比",
        )
    else:
        result["reason"] = "; ".join(inspection["problems"])
        # 演练失败是 **error** 级：它意味着"当前没有可用的备份"
        logger.error("恢复演练失败: %s | %s", snapshot, result["reason"])
    return result


__all__ = [
    "BACKUP_ROOT", "DEFAULT_DB",
    "create_snapshot", "verify_snapshot", "prune_snapshots",
    "list_snapshots", "resolve_db_path", "run_scheduled_backup",
    "inspect_snapshot", "run_restore_drill",
]
