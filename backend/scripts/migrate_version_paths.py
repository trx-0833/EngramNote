# -*- coding: utf-8 -*-
"""迁移版本存储路径：{P}/history/versions/v{N}.md → {P}/history/versions/{note_id}/v{N}.md

背景：旧路径不含 note_id，不同笔记的同号版本（如 v1）共享同一物理文件，互相覆盖。
本脚本将每条版本记录的物理文件复制到按 note_id 隔离的新路径并更新 DB 记录，
迁移完成后旧的 versions 目录整体移动到备份目录保留可恢复性。

注意：旧文件可能被多条记录共享（重名覆盖的历史遗留），复制时均从同一旧文件取内容，
迁移后每条记录拥有独立文件，不再互相影响。

用法：python scripts/migrate_version_paths.py [--dry-run]
"""
import argparse
import shutil
import sys
import time
from pathlib import Path

import sqlite3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DB = Path(__file__).resolve().parent.parent / "data/db/engramnote.db"
STORAGE = Path(__file__).resolve().parent.parent / "data/storage"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = list(con.execute(
        "SELECT id, note_id, user_id, version_number, storage_path FROM note_versions ORDER BY user_id, note_id, version_number"
    ))
    print(f"共 {len(rows)} 条版本记录\n")

    old_version_dirs = set()
    copied = 0
    for r in rows:
        old_path = r["storage_path"]
        if not old_path:
            print(f"[skip] {r['id'][:8]} 无 storage_path")
            continue
        if "/history/versions/" not in old_path:
            print(f"[skip] {r['id'][:8]} 非 Vault 版本路径: {old_path}")
            continue

        # 新路径：{P}/history/versions/{note_id}/v{N}.md
        parts = old_path.split("/history/versions/")
        prefix = parts[0]
        new_path = f"{prefix}/history/versions/{r['note_id']}/v{r['version_number']}.md"
        old_abs = STORAGE / old_path.replace("/", "\\")
        new_abs = STORAGE / new_path.replace("/", "\\")
        old_version_dirs.add(old_abs.parent)

        print(f"[plan] {r['note_id'][:8]} v{r['version_number']}: {old_path} -> {new_path}")

        if args.dry_run:
            continue

        if old_abs.exists() and old_abs.is_file():
            new_abs.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(old_abs), str(new_abs))
            copied += 1
        else:
            print(f"[warn] 物理文件不存在: {old_abs}")

        con.execute("UPDATE note_versions SET storage_path=? WHERE id=?", (new_path, r["id"]))

    if args.dry_run:
        print("\n[dry-run] 未执行任何变更")
        return

    con.commit()
    # 统一备份旧 versions 目录（若所有记录已迁移）
    bak_root = STORAGE / f"_legacy_version_cleanup_{time.strftime('%Y%m%d')}"
    for vdir in sorted(old_version_dirs, key=lambda p: str(p)):
        if vdir.exists() and vdir.is_dir():
            dst = bak_root / vdir.relative_to(STORAGE).as_posix()
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(vdir), str(dst))
            print(f"[bak] {vdir} -> {dst}")
    print(f"\n[ok] 已复制 {copied} 个版本文件并更新 {len(rows)} 条记录；旧目录备份于 {bak_root}")


if __name__ == "__main__":
    main()
