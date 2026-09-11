"""
破坏性 schema 重建的一次性入口（显式执行）

## 为什么需要这个脚本

`app/database.py::_rebuild_dangling_tables()` 用于把若干张表的**外键端改为
可空**（回收站悬挂引用改造），以及本次新增的 `review_logs.quiz_id` 可空
（阶段 3.12 卡片直接复习需要）。

SQLite 不支持 `ALTER COLUMN ... DROP NOT NULL`，只能"建新表 → 拷数据 → 改名"，
而 `PRAGMA foreign_keys` 是连接级开关且不能在事务内切换，所以它是一个
会 `DROP` 业务表的操作。

阶段 0 出于数据安全把它**从启动路径移除**了（原先 API / worker / beat
三个进程启动时都会执行，任意中断都可能丢数据）。移除是对的，但副作用是
**它此后从未被调用过** —— 于是 `review_logs.quiz_id` 始终停留在旧的
NOT NULL schema，而全新库是正确的（本轮实测对照确认）。

本脚本把这个操作重新接上，但以**显式、单次、可校验**的方式：

    备份 → 探测 → 重建 → 逐项校验 → 失败可回退

## 为什么不做成 `init_db()` 的一部分

启动路径必须是**无损**的：`create_all()`（只建缺失的表）与加列（只加不删）
是安全的，DROP/重建不是。把重建放回启动路径会重新引入
"一次断电丢数据"的风险，而收益只是省一条命令 —— 不划算。

## 用法（在 backend/ 目录下）

    python scripts/reconcile_schema.py --check     # 只探测，不改动
    python scripts/reconcile_schema.py             # 实际重建（会自动先备份）
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

#: 与 database.py 的 targets 保持一致的探测清单
#: (表名, 探测列, 期望的 notnull 值, 变更原因)
PROBES = (
    ("card_relations", "card_id_1", 0, "回收站悬挂引用：外键端改可空 + ON DELETE SET NULL"),
    ("note_material_links", "personal_note_id", 0, "回收站悬挂引用：外键端改可空"),
    ("knowledge_cards", "note_id", 0, "回收站悬挂引用：外键端改可空"),
    ("quiz_items", "note_id", 0, "回收站悬挂引用：外键端改可空"),
    ("review_logs", "note_id", 0, "回收站悬挂引用：外键端改可空"),
    ("review_logs", "quiz_id", 0, "阶段 3.12：卡片级复习没有题目，该列必须可空"),
)


def _db_path() -> Path:
    from app.config import DB_DIR

    return DB_DIR / "engramnote.db"


def _snapshot_counts(path: Path) -> dict[str, int]:
    """关键表行数快照（重建前后必须一致）"""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        counts = {}
        for (table,) in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ):
            counts[table] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return counts
    finally:
        con.close()


def _fk_violations(path: Path) -> set[tuple]:
    """当前的外键违规集合

    用途是**对比**而不是判零：库里本来就存在历史孤儿
    （实测 `note_material_links` 有一条 user_id 指向已删除用户的行，
    正是 overhaul-plan 附录 A.7 记录的那笔历史问题）。
    要求"零违规"会让脚本对一笔与本次重建无关的历史数据永远报失败，
    正确的判据是"本次重建没有**新增**违规"。
    """
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {tuple(row) for row in con.execute("PRAGMA foreign_key_check")}
    finally:
        con.close()


def _probe(path: Path) -> list[tuple[str, str, int, int, str]]:
    """返回需要重建的 (表, 列, 当前notnull, 期望notnull, 原因)"""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        pending = []
        for table, column, expected, reason in PROBES:
            row = next(
                (c for c in con.execute(f"PRAGMA table_info({table})") if c[1] == column),
                None,
            )
            if row is None:
                continue  # 表/列不存在（全新库不会有问题）
            if bool(row[3]) != bool(expected):
                pending.append((table, column, int(row[3]), expected, reason))
        return pending
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="破坏性 schema 重建（显式、带校验）")
    parser.add_argument("--check", action="store_true", help="只探测，不做任何改动")
    parser.add_argument("--no-backup", action="store_true", help="跳过自动备份（不推荐）")
    args = parser.parse_args()

    path = _db_path()
    if not path.exists():
        print(f"错误: 数据库不存在: {path}", file=sys.stderr)
        return 1

    pending = _probe(path)
    if not pending:
        print("schema 已符合预期，无需重建。")
        return 0

    print("检测到需要重建的列：")
    for table, column, current, expected, reason in pending:
        print(f"  {table}.{column}: notnull={current} -> {expected}   ({reason})")

    if args.check:
        print("\n（--check 模式，未做任何改动）")
        print("如需执行: python scripts/reconcile_schema.py")
        return 0

    # 1. 备份（失败则中止 —— 没有回退手段时不做破坏性操作）
    if not args.no_backup:
        from app.services.backup_service import create_snapshot

        try:
            target = create_snapshot(path, label="pre-schema-rebuild")
            print(f"\n已创建回退快照: {target}")
        except Exception as exc:
            print(f"错误: 备份失败，已中止（不做无回退的破坏性操作）: {exc}", file=sys.stderr)
            return 2

    before = _snapshot_counts(path)
    fk_before = _fk_violations(path)

    # 2. 放行闸门并重建
    #
    # `_rebuild_dangling_tables()` 自带 `_destructive_migration_allowed()` 检查，
    # 未放行时只探测告警。这里通过环境变量显式放行 —— 本脚本本身就是
    # "显式执行"的入口，闸门的作用是防止它随进程启动自动发生。
    os.environ["ENGRAMNOTE_ALLOW_DESTRUCTIVE_MIGRATION"] = "1"

    from app import database as db_mod

    async def _run() -> None:
        await db_mod._rebuild_dangling_tables()
        await db_mod.get_engine().dispose()

    asyncio.run(_run())

    # 3. 逐项校验
    print("\n=== 重建后校验 ===")
    still_pending = _probe(path)
    after = _snapshot_counts(path)

    ok = True
    if still_pending:
        ok = False
        print("错误: 以下列仍未达到预期 schema:")
        for table, column, current, expected, _ in still_pending:
            print(f"  {table}.{column}: notnull={current}（期望 {expected}）")

    lost = {t: (before[t], after.get(t)) for t in before if before[t] != after.get(t)}
    if lost:
        ok = False
        print("错误: 以下表行数发生变化（疑似数据丢失）:")
        for table, (b, a) in lost.items():
            print(f"  {table}: {b} -> {a}")
    else:
        print(f"行数校验通过：{len(before)} 张表全部未变")

    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        con.close()
    print(f"integrity_check = {integrity}")

    fk_after = _fk_violations(path)
    new_violations = fk_after - fk_before
    if new_violations:
        ok = False
        print(f"错误: 重建引入了新的外键违规: {sorted(new_violations)[:5]}")
    elif fk_after:
        # 历史孤儿：如实报告，但不判为本次失败
        print(
            f"外键校验：无新增违规（库里仍存在 {len(fk_after)} 条**历史**孤儿，"
            f"与本次重建无关，例如 {sorted(fk_after)[:2]}）"
        )
    else:
        print("外键校验通过（无任何违规）")

    print("\n" + ("=== 重建成功 ===" if ok else "=== 重建未完全成功，请用上面的快照回退 ==="))
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
