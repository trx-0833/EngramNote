"""
数据库备份 CLI（薄壳）

**核心逻辑在 `app/services/backup_service.py`**，本文件只做参数解析与输出。
抽出去的原因：备份不只要能手动跑，还要能被 Celery Beat 定时调用
（阶段 1′ 第 5 项）。若逻辑留在这里，任务侧就得反过来 import scripts —— 
依赖方向是错的，且两处实现必然漂移。

为什么用 `VACUUM INTO` 而不是复制文件：
1. **原子一致**：源库正在被 API / Celery worker 写入时，直接 `copy` 可能拿到
   "半写完"的页组合（WAL 模式下还有未 checkpoint 的 -wal 文件）。
   `VACUUM INTO` 由 SQLite 自己保证读到一致快照。
2. **自动整理**：输出是紧凑的、无碎片的库文件，恢复后更快。
3. **零依赖**：不需要额外安装任何包，也不需要停服务。

用法（在 backend/ 目录下）：
    python scripts/backup_db.py                 # 备份到仓库根 _backup/<时间戳>/
    python scripts/backup_db.py --label pre-selfrating
    python scripts/backup_db.py --keep 5        # 只保留最近 5 份 db 快照
    python scripts/backup_db.py --list          # 列出已有快照（含完整性状态）

恢复方式：
    见 scripts/restore_db.py（会先校验快照完整性，再备份当前库，最后替换）。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# backend/scripts/backup_db.py -> backend/
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.backup_service import (  # noqa: E402
    create_snapshot,
    list_snapshots,
    prune_snapshots,
    resolve_db_path,
    verify_snapshot,
)


def _print_list() -> None:
    items = list_snapshots()
    if not items:
        print("尚无备份")
        return
    for item in items:
        if not item.get("has_db"):
            print(f"  {item['name']:<32} (无 db 快照)")
            continue
        size_mb = (item.get("size_bytes") or 0) / 1024 / 1024
        integrity = item.get("integrity") or item.get("verify_error") or "未校验"
        counts = item.get("counts") or {}
        detail = ", ".join(f"{k}={v}" for k, v in counts.items() if v is not None)
        print(f"  {item['name']:<32} {size_mb:>7.1f} MB  integrity={integrity}  {detail}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="EngramNote 数据库备份（VACUUM INTO 原子快照）"
    )
    parser.add_argument("--db", help="指定数据库文件（默认 backend/data/db/engramnote.db）")
    parser.add_argument("--label", help="快照标签，便于识别（如 pre-migration）")
    parser.add_argument("--keep", type=int, default=0, help="只保留最近 N 份 db 快照（0=不清理）")
    parser.add_argument("--list", action="store_true", help="列出已有快照后退出")
    args = parser.parse_args()

    if args.list:
        _print_list()
        return 0

    db_file = resolve_db_path(args.db)
    print(f"源库: {db_file}")
    if not db_file.exists():
        print("错误: 源库不存在", file=sys.stderr)
        return 1

    target = create_snapshot(db_file, args.label)
    info = verify_snapshot(target)
    size_mb = target.stat().st_size / 1024 / 1024
    print(f"快照: {target}  ({size_mb:.1f} MB)")
    print(f"integrity_check = {info['integrity']}")
    for key, value in info["counts"].items():
        print(f"  {key:<16} {value}")

    if info["integrity"] != "ok":
        print("错误: 快照完整性校验未通过，请勿依赖该备份", file=sys.stderr)
        return 2

    if args.keep:
        removed = prune_snapshots(args.keep)
        if removed:
            print(f"已清理 {len(removed)} 份旧快照: {', '.join(removed)}")

    return 0


if __name__ == "__main__":
    # 允许通过环境变量固定保留份数，便于定时任务与手动备份共用策略
    keep_default = int(os.environ.get("ENGRAMNOTE_BACKUP_KEEP", "0") or 0)
    if keep_default and "--keep" not in sys.argv:
        sys.argv.extend(["--keep", str(keep_default)])
    raise SystemExit(main())
