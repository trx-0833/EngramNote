"""
数据库恢复脚本（恢复演练的正规入口）

## 为什么恢复要单独写一个脚本，而不是"把快照覆盖回去"

文档里原先只写了一句"停掉服务，把快照覆盖回 data/db/engramnote.db 即可"。
这句话漏掉了三个会让人丢数据的坑：

1. **WAL 兄弟文件**：只覆盖主库、留下旧的 `-wal` / `-shm`，SQLite 启动时
   会把旧 WAL 里的**过期事务**重放到新库上 —— 得到的既不是快照状态，
   也不是覆盖前状态。必须先删干净。
2. **不校验就恢复**：静默损坏的快照覆盖上去，等于用坏数据换掉好数据。
   恢复前必须在快照上跑 `integrity_check`，不通过就中止。
3. **不可回退**：恢复是破坏性操作。恢复前自动为"当前库"再存一份快照，
   万一选错了源，还能退回去。

用法（在 backend/ 目录下）：
    python scripts/restore_db.py --list                     # 列出可选快照
    python scripts/restore_db.py --from 20260911-003512-pre-selfrating
    python scripts/restore_db.py --from <快照目录名> --yes     # 跳过二次确认
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.backup_service import (  # noqa: E402
    BACKUP_ROOT,
    create_snapshot,
    list_snapshots,
    resolve_db_path,
    verify_snapshot,
)

#: WAL 模式下的兄弟文件；恢复时必须一并删除，
#: 否则 SQLite 会把旧 WAL 中的过期事务重放到新库上
_WAL_SIBLINGS = ("-wal", "-shm")


def _print_list() -> None:
    items = [i for i in list_snapshots() if i.get("has_db")]
    if not items:
        print("没有可用的 db 快照")
        return
    for item in items:
        size_mb = (item.get("size_bytes") or 0) / 1024 / 1024
        integrity = item.get("integrity") or "未校验"
        counts = item.get("counts") or {}
        detail = ", ".join(f"{k}={v}" for k, v in counts.items() if v is not None)
        print(f"  {item['name']:<32} {size_mb:>7.1f} MB  integrity={integrity}  {detail}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="从快照恢复 EngramNote 数据库（会先校验快照、再备份当前库）"
    )
    parser.add_argument("--from", dest="source", help="快照目录名（见 --list）")
    parser.add_argument("--db", help="目标数据库文件（默认 backend/data/db/engramnote.db）")
    parser.add_argument("--list", action="store_true", help="列出可用快照后退出")
    parser.add_argument("--yes", action="store_true", help="跳过交互式确认（脚本化场景）")
    args = parser.parse_args()

    if args.list:
        _print_list()
        return 0

    if not args.source:
        _print_list()
        print("\n请用 --from <快照目录名> 指定要恢复的快照", file=sys.stderr)
        return 1

    snapshot_dir = BACKUP_ROOT / args.source
    snapshot_db = snapshot_dir / "engramnote.db"
    if not snapshot_db.exists():
        print(f"错误: 快照不存在: {snapshot_db}", file=sys.stderr)
        return 1

    # 1. 先校验快照本身 —— 坏快照绝不能覆盖好数据
    info = verify_snapshot(snapshot_db)
    print(f"快照: {snapshot_db}")
    print(f"integrity_check = {info['integrity']}")
    for key, value in info["counts"].items():
        print(f"  {key:<16} {value}")
    if info["integrity"] != "ok":
        print(
            "错误: 该快照完整性校验未通过，已中止恢复（不会破坏当前库）",
            file=sys.stderr,
        )
        return 2

    target = resolve_db_path(args.db)
    print(f"\n目标库: {target}")

    # 2. 二次确认：恢复是破坏性操作
    if not args.yes:
        answer = input("将用快照覆盖目标库，当前内容会先被另存一份。继续？[y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("已取消")
            return 0

    # 3. 先把"当前库"存一份，保证可回退
    if target.exists():
        try:
            pre = create_snapshot(target, label="pre-restore")
            print(f"已为当前库创建回退快照: {pre}")
        except Exception as exc:
            print(f"错误: 无法为当前库创建回退快照，已中止: {exc}", file=sys.stderr)
            return 3

    # 4. 删除 WAL 兄弟文件后覆盖
    for suffix in _WAL_SIBLINGS:
        sibling = Path(str(target) + suffix)
        if sibling.exists():
            sibling.unlink()
            print(f"已删除 {sibling.name}（避免旧事务被重放）")

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(snapshot_db, target)

    # 5. 恢复后校验目标库
    after = verify_snapshot(target)
    print(f"\n恢复完成: {target}")
    print(f"integrity_check = {after['integrity']}")
    for key, value in after["counts"].items():
        print(f"  {key:<16} {value}")

    if after["integrity"] != "ok":
        print(
            "警告: 恢复后的库完整性校验未通过。请立即用上一步的回退快照还原。",
            file=sys.stderr,
        )
        return 4

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
