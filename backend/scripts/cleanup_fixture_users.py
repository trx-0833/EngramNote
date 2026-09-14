# -*- coding: utf-8 -*-
"""清理遗留测试夹具用户 u1 / u2 · 一次性运维脚本

## 删除对象（上一次清理已确认，见 backend/data/db/cleanup-notes-n1-n2.log §8）

  u1 / u2 是测试套件早期直接写进生产库的夹具账号（`hashed_password` 字面量
  就是 `"x"`）。上一次清理删掉了它们名下仅有的两张笔记 n1/n2 及卡片后，
  它们只剩占位行：

      folders        2 行   f1 (u1), f2 (u2)
      learning_goals 1 行   35a1f6b5-… (u1, name 目标1, scope_notes 已是 [])
      users          2 行   u1, u2
      ------ 共 5 行，3 张表

## 协议（与上一次清理完全一致，不可省）

1. `--dry-run`：**先枚举、再删除**。逐表逐列探测目标 id 的引用（包括
   `json_each` 探测 JSON 数组列、Python 侧递归探测嵌套 JSON 对象列、
   `LIKE` 文本扫尾），输出待删清单与每张表的预测行数。**发现任何计划外的
   引用即中止**（退出码 2），不猜、不跳过。
2. `--apply`：必须先由人取好快照；脚本会校验该快照存在且 `_verify.json`
   里 `integrity == ok`，否则中止。删除在**一个** `BEGIN IMMEDIATE` 事务里
   完成，**每条语句的 rowcount 必须等于 dry-run 预测**，事务内再校验
   `integrity_check` 与 `foreign_key_check` 与删除前**逐字节一致**，否则回滚。
3. 不调用 `init_db()`：`app/database.py::_migrate_sqlite()` 带无条件写路径
   （见上一次清理 §5 / T6），会写入批准范围之外的行。

## 为什么不复用 scripts/cleanup_test_data.py

那个脚本按"保留名单"删账号，会连真实用户一起卷进来，且没有 JSON 引用探测
与逐语句 rowcount 断言。本脚本只认这 5 个 id，且任何计划外引用都会中止。

用法（backend 目录下）：

    python scripts/cleanup_fixture_users.py --dry-run
    python scripts/cleanup_fixture_users.py --dry-run --json-out dry.json
    python scripts/cleanup_fixture_users.py --apply --snapshot <快照db路径> --expect dry.json

退出码：0 成功；1 执行失败（已回滚）；2 前置检查/发现计划外引用而中止。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DB_DIR  # noqa: E402

#: 目标夹具用户
FIXTURE_USER_IDS = ("u1", "u2")

#: 删除顺序：子表 → 父表（PRAGMA foreign_keys=ON 时顺序反了会被约束拦住）
DELETE_ORDER = ("learning_goals", "folders", "users")

#: 允许出现的引用位置（表, 列）。其余任何命中都是"计划外引用" → 中止。
EXPECTED_REF_COLUMNS = {
    ("users", "id"),
    ("folders", "id"),
    ("folders", "user_id"),
    ("folders", "name"),
    ("learning_goals", "id"),
    ("learning_goals", "user_id"),
    ("learning_goals", "scope_folders"),
}


def _configure_console() -> None:
    """Windows 中文控制台默认 GBK，打印非 GBK 字符会让整份报告变成 traceback"""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _connect(db_path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    """按 app/database.py::_set_sqlite_pragma 的同一组 pragma 打开连接

    `foreign_keys` 是**每连接**的，默认 OFF；不显式打开就没有任何约束兜底。
    `isolation_level=None` 关闭 sqlite3 模块的隐式事务管理，
    这样 `BEGIN IMMEDIATE` 才是我们自己的一个事务。
    """
    if read_only:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    else:
        con = sqlite3.connect(str(db_path), isolation_level=None, timeout=30)
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("PRAGMA synchronous=NORMAL")
    con.row_factory = sqlite3.Row
    return con


def _tables(cur: sqlite3.Cursor) -> list[str]:
    return [
        r[0]
        for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def _columns(cur: sqlite3.Cursor, table: str) -> list[str]:
    return [r[1] for r in cur.execute("PRAGMA table_info('%s')" % table)]


def _discover_target_ids(cur: sqlite3.Cursor) -> dict[str, list[str]]:
    """目标 id 集合：用户 → 其名下 folder / learning_goal（不假设、不硬编码 id）"""
    users = [r[0] for r in cur.execute("SELECT id FROM users ORDER BY id")]
    targets: dict[str, list[str]] = {"users": [], "folders": [], "learning_goals": []}
    for uid in FIXTURE_USER_IDS:
        if uid in users:
            targets["users"].append(uid)
    if targets["users"]:
        marks = ",".join("?" * len(targets["users"]))
        targets["folders"] = [
            r[0]
            for r in cur.execute(
                "SELECT id FROM folders WHERE user_id IN (%s) ORDER BY id" % marks,
                targets["users"],
            )
        ]
        targets["learning_goals"] = [
            r[0]
            for r in cur.execute(
                "SELECT id FROM learning_goals WHERE user_id IN (%s) ORDER BY id" % marks,
                targets["users"],
            )
        ]
    return targets


def scan_references(
    cur: sqlite3.Cursor, target_ids: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """逐表逐列探测对目标 id 的引用

    Returns:
        (exact_hits, json_hits)

    - `exact_hits`：`列 = id` 的精确命中（覆盖字符串主键/外键列）；
    - `json_hits`：JSON 列内命中的 id。JSON 列**不是**外键，`foreign_key_check`
      看不见它们（见上一次清理 T4），因此这里用 `json_each` 绑定**表别名**的值
      ——写成 `json_each(表.列)` 会让 SQLite 重读列本身，静默返回 0。
      非数组的 JSON 列（对象）改用 Python 侧递归遍历。
    """
    exact: list[dict[str, Any]] = []
    jsons: list[dict[str, Any]] = []

    for table in _tables(cur):
        for column in _columns(cur, table):
            for tid in target_ids:
                try:
                    n = cur.execute(
                        'SELECT COUNT(*) FROM "%s" WHERE "%s" = ?' % (table, column),
                        (tid,),
                    ).fetchone()[0]
                except sqlite3.Error:
                    continue  # 类型/虚拟列等无法比较，跳过（JSON 由下面的分支覆盖）
                if n:
                    exact.append(
                        {"table": table, "column": column, "value": tid, "rows": n}
                    )

            # JSON 列：任一行以 [ 或 { 开头即视为 JSON
            try:
                sample_sql = (
                    'SELECT "' + column + '" FROM "' + table + '" WHERE "'
                    + column
                    + '" IS NOT NULL AND (CAST("'
                    + column
                    + "\" AS TEXT) LIKE '[%' OR CAST(\""
                    + column
                    + "\" AS TEXT) LIKE '{%') LIMIT 1"
                )
                sample = cur.execute(sample_sql).fetchone()
            except sqlite3.Error:
                sample = None
            if sample is None:
                continue
            try:
                values = cur.execute(
                    'SELECT rowid, "%s" FROM "%s" WHERE "%s" IS NOT NULL'
                    % (column, table, column)
                ).fetchall()
            except sqlite3.Error:
                continue
            for rowid, raw in values:
                try:
                    payload = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                found = _walk_json(payload, set(target_ids))
                if found:
                    jsons.append(
                        {
                            "table": table,
                            "column": column,
                            "rowid": rowid,
                            "values": sorted(found),
                            "rows": 1,
                        }
                    )
    return exact, jsons


def _walk_json(node: Any, targets: set[str]) -> set[str]:
    """递归遍历 JSON 结构，收集等于目标 id 的**标量**"""
    found: set[str] = set()
    if isinstance(node, str):
        if node in targets:
            found.add(node)
    elif isinstance(node, dict):
        for value in node.values():
            found |= _walk_json(value, targets)
    elif isinstance(node, list):
        for value in node:
            found |= _walk_json(value, targets)
    return found


def _counts(cur: sqlite3.Cursor) -> dict[str, int]:
    out: dict[str, int] = {}
    for table in _tables(cur):
        out[table] = cur.execute('SELECT COUNT(*) FROM "%s"' % table).fetchone()[0]
    return out


def _fk_rows(cur: sqlite3.Cursor) -> list[tuple]:
    return [tuple(r) for r in cur.execute("PRAGMA foreign_key_check")]


def build_plan(cur: sqlite3.Cursor) -> dict[str, Any]:
    """dry-run：枚举待删行、预测每张表的行数，并证明没有计划外引用"""
    targets = _discover_target_ids(cur)
    all_ids = [i for group in targets.values() for i in group]
    exact, jsons = scan_references(cur, all_ids)

    plan: list[dict[str, Any]] = []
    user_marks = ",".join("?" * len(targets["users"]))
    for table in DELETE_ORDER:
        # 子表的谓词是 `user_id IN (u1,u2)`，users 表自身是 `id IN (u1,u2)`；
        # 两种情况的**参数都是用户 id**，取出的才是待删行自己的 id。
        column = "id" if table == "users" else "user_id"
        rows = [
            r[0]
            for r in cur.execute(
                'SELECT id FROM "%s" WHERE %s IN (%s) ORDER BY id' % (table, column, user_marks),
                targets["users"],
            )
        ]
        plan.append(
            {
                "table": table,
                "column": column,
                #: 谓词参数 = **用户 id**（不是待删行自己的 id）
                "params": list(targets["users"]),
                #: 待删行自己的 id（用于事后核对，也用于生成回滚语句）
                "ids": rows,
                "count": len(rows),
            }
        )

    unexpected = [
        hit for hit in exact if (hit["table"], hit["column"]) not in EXPECTED_REF_COLUMNS
    ]
    unexpected_json = [
        hit
        for hit in jsons
        if (hit["table"], hit["column"]) not in EXPECTED_REF_COLUMNS
    ]

    # 计划内引用是否确实只落在待删行上（防止"表对了、行不对"）
    planned_ids = {i for entry in plan for i in entry["ids"]}
    for hit in exact + jsons:
        if (hit["table"], hit["column"]) in EXPECTED_REF_COLUMNS and hit.get("value") not in (
            None,
            *planned_ids,
        ):
            unexpected.append({**hit, "reason": "引用了计划外的行"})

    return {
        "fixture_user_ids": list(FIXTURE_USER_IDS),
        "targets": targets,
        "plan": plan,
        "total_rows": sum(entry["count"] for entry in plan),
        "exact_hits": exact,
        "json_hits": jsons,
        "unexpected": unexpected + unexpected_json,
        "counts_before": _counts(cur),
        "fk_before": _fk_rows(cur),
        "integrity_before": cur.execute("PRAGMA integrity_check").fetchone()[0],
        "journal_mode_before": cur.execute("PRAGMA journal_mode").fetchone()[0],
    }


def print_plan(analysis: dict[str, Any]) -> None:
    bar = "=" * 78
    print(bar)
    print("dry-run：遗留测试夹具用户 u1 / u2 待删清单")
    print(bar)
    print("目标 id：")
    for table, ids in analysis["targets"].items():
        print("  %-15s %s" % (table, ids or "（无）"))
    print("-" * 78)
    print("待删行（子表 → 父表）：")
    print("  %-17s %-16s %6s   %s" % ("table", "predicate", "rows", "ids"))
    for entry in analysis["plan"]:
        print(
            "  %-17s %-16s %6d   %s"
            % (
                entry["table"],
                "%s IN (u1,u2)" % entry["column"]
                if entry["table"] != "users"
                else "id IN (u1,u2)",
                entry["count"],
                ", ".join(entry["ids"]),
            )
        )
    print("  %-17s %-16s %6d" % ("合计", "", analysis["total_rows"]))
    print("-" * 78)
    print("全部引用命中（逐表逐列精确探测 + JSON 递归探测）：")
    if not analysis["exact_hits"] and not analysis["json_hits"]:
        print("  （无）")
    for hit in analysis["exact_hits"]:
        print(
            "  exact  %s.%s = %r  -> %d 行"
            % (hit["table"], hit["column"], hit["value"], hit["rows"])
        )
    for hit in analysis["json_hits"]:
        print(
            "  json   %s.%s (rowid=%s) 含 %s"
            % (hit["table"], hit["column"], hit["rowid"], hit["values"])
        )
    print("-" * 78)
    print("数据库检查：")
    print("  integrity_check   :", analysis["integrity_before"])
    print("  foreign_key_check :", analysis["fk_before"] or "（空）")
    print("  journal_mode      :", analysis["journal_mode_before"])
    print("-" * 78)
    if analysis["unexpected"]:
        print("[ABORT] 发现计划外引用，未做任何修改：")
        for hit in analysis["unexpected"]:
            print("   ", hit)
    else:
        print(
            "[OK] 没有任何计划外引用：除待删清单中的 %d 行外，全库不存在对 %s 及其名下 "
            "folder / 学习目标 id 的引用。"
            % (analysis["total_rows"], "/".join(analysis["fixture_user_ids"]))
        )
    print(bar)


def check_preconditions(db_path: Path, snapshot: Path | None) -> None:
    """前置检查：单一写者、WAL 已 checkpoint、快照完好"""
    wal = Path(str(db_path) + "-wal")
    if wal.exists() and wal.stat().st_size > 0:
        raise SystemExit("[ABORT] %s 非空（有未 checkpoint 的写入），先确认没有服务在写库" % wal)
    if snapshot is None:
        raise SystemExit(
            "[ABORT] --apply 必须显式给出 --snapshot <快照db路径>（先取快照是硬性前置）"
        )
    if not snapshot.exists():
        raise SystemExit("[ABORT] 快照不存在: %s" % snapshot)
    verify_file = snapshot.parent / "_verify.json"
    if not verify_file.exists():
        raise SystemExit("[ABORT] 快照缺少 _verify.json，无法确认完整性: %s" % verify_file)
    info = json.loads(verify_file.read_text(encoding="utf-8"))
    print("快照      :", snapshot)
    print("  标签    :", info.get("label"))
    print("  完整性  :", info.get("integrity"), "| 行数:", info.get("counts"))
    if info.get("integrity") != "ok":
        raise SystemExit("[ABORT] 快照 integrity != ok，拒绝删除")
    if Path(info.get("snapshot", "")) != snapshot:
        print("  [warn] _verify.json 记录的路径与传入路径不同，按传入路径继续")


def compare_with_expect(analysis: dict[str, Any], expect_path: Path) -> None:
    """apply 前把本次枚举结果与人工审阅过的 dry-run JSON 对齐"""
    expected = json.loads(expect_path.read_text(encoding="utf-8"))
    mine = {e["table"]: (e["count"], e["ids"], e.get("params")) for e in analysis["plan"]}
    theirs = {
        e["table"]: (e["count"], e["ids"], e.get("params")) for e in expected["plan"]
    }
    if mine != theirs:
        raise SystemExit(
            "[ABORT] 本次枚举与 dry-run 预测不一致：\n  现在: %s\n  预期: %s" % (mine, theirs)
        )
    print("[OK] 本次枚举与 dry-run 预测一致：", mine)


def apply_delete(con: sqlite3.Connection, analysis: dict[str, Any]) -> None:
    """一个事务里删除，逐语句断言 rowcount，事务内复验完整性/外键"""
    cur = con.cursor()
    fk_before = analysis["fk_before"]
    integrity_before = analysis["integrity_before"]

    cur.execute("BEGIN IMMEDIATE")
    try:
        for entry in analysis["plan"]:
            marks = ",".join("?" * len(entry["params"]))
            column = entry["column"]
            sql = 'DELETE FROM "%s" WHERE %s IN (%s)' % (entry["table"], column, marks)
            cur.execute(sql, entry["params"])
            if cur.rowcount != entry["count"]:
                raise RuntimeError(
                    "rowcount 不符：%s 预期 %d 实际 %d"
                    % (entry["table"], entry["count"], cur.rowcount)
                )
            print(
                "  DELETE %-17s WHERE %s IN %s -> rowcount=%d（预期 %d）OK"
                % (entry["table"], column, tuple(entry["params"]), cur.rowcount, entry["count"])
            )

        integrity_now = cur.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity_now != "ok" or integrity_now != integrity_before:
            raise RuntimeError("事务内 integrity_check 变了: %s -> %s" % (integrity_before, integrity_now))

        fk_now = _fk_rows(cur)
        if fk_now != fk_before:
            raise RuntimeError("事务内 foreign_key_check 变了: %s -> %s" % (fk_before, fk_now))

        cur.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    print("  COMMIT 完成；事务内 integrity_check=ok，foreign_key_check 与删除前逐字节一致")


async def _audit_vault() -> dict[str, Any]:
    """用 app 自己的引擎跑 audit_vault（与每周维护任务同一条读路径）"""
    from app.database import get_engine, get_session_factory
    from app.services.vault_audit_service import audit_vault

    factory = get_session_factory()
    try:
        async with factory() as session:
            result = await audit_vault(session, deep=False, include_orphans=True)
            return result.to_dict()
    finally:
        await get_engine().dispose()


def main() -> int:
    _configure_console()
    parser = argparse.ArgumentParser(description="清理遗留测试夹具用户 u1 / u2")
    parser.add_argument("--dry-run", action="store_true", help="只读枚举待删清单")
    parser.add_argument("--apply", action="store_true", help="执行删除（需 --snapshot）")
    parser.add_argument("--snapshot", default=None, help="删除前取好的快照 db 路径（--apply 必填）")
    parser.add_argument(
        "--expect", default=None,
        help="人工审阅过的 --dry-run --json-out 文件；apply 前逐行比对预测",
    )
    parser.add_argument("--json-out", default=None, help="把 dry-run 结果写成 JSON")
    parser.add_argument("--db", default=str(DB_DIR / "engramnote.db"), help="目标库路径")
    args = parser.parse_args()

    if args.dry_run == args.apply:
        parser.error("必须且只能指定 --dry-run 或 --apply 之一")

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit("[ABORT] 数据库不存在: %s" % db_path)

    if args.dry_run:
        con = _connect(db_path, read_only=True)
        try:
            analysis = build_plan(con.cursor())
        finally:
            con.close()
        print_plan(analysis)
        if args.json_out:
            Path(args.json_out).write_text(
                json.dumps(analysis, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            print("dry-run JSON 已写入", args.json_out)
        return 2 if analysis["unexpected"] else 0

    # ---- apply ----
    snapshot = Path(args.snapshot) if args.snapshot else None
    check_preconditions(db_path, snapshot)

    con = _connect(db_path)
    try:
        cur = con.cursor()
        analysis = build_plan(cur)
        print_plan(analysis)
        if analysis["unexpected"]:
            print("[ABORT] 有计划外引用，未做任何修改", file=sys.stderr)
            return 2
        if args.expect:
            compare_with_expect(analysis, Path(args.expect))
        if not analysis["total_rows"]:
            print("[OK] 没有待删行（已清理过），未做任何修改")
            return 0

        apply_delete(con, analysis)
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")

        counts_after = _counts(con)
        print("-" * 78)
        print("行数 before -> after（只列变化的表）：")
        for table, after in counts_after.items():
            before = analysis["counts_before"].get(table)
            if before != after:
                print("  %-20s %6s -> %-6s (%+d)" % (table, before, after, after - before))
        print("  foreign_key_check :", _fk_rows(con) or "（空）")
        print("  integrity_check   :", cur.execute("PRAGMA integrity_check").fetchone()[0])
        print("  quick_check       :", cur.execute("PRAGMA quick_check").fetchone()[0])
        print("  FTS5 integrity    :", end=" ")
        cur.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('integrity-check')")
        print("ok")
    finally:
        con.close()

    print("-" * 78)
    print("audit_vault（app 自己的引擎 + app 自己的审计函数）：")
    print(json.dumps(asyncio.run(_audit_vault()), ensure_ascii=False, indent=2))
    print("=" * 78)
    print("完成。回滚见 backend/data/db/cleanup-notes-n1-n2.log §11。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
