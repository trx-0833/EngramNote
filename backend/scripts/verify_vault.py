"""
Vault 一致性校验 CLI（overhaul-plan 阶段 1.11）

回答一个此前无法回答的问题：**磁盘上的文件与数据库的记录还一致吗？**

    python scripts/verify_vault.py                  # 全量校验（按大小）
    python scripts/verify_vault.py --deep           # 逐文件比对 sha256（慢）
    python scripts/verify_vault.py --user <id>      # 只校验某用户
    python scripts/verify_vault.py --json           # 输出 JSON（供 CI/监控消费）

**默认只报告，不修改。** 与 `_migrate_sqlite` 的孤儿检查同一原则：
校验器一旦自动"修复"，就会把一次误判变成不可逆的数据删除。

退出码：
    0 = 无不一致
    1 = 发现不一致（便于 CI / 监控告警）
    2 = 校验本身失败（例如数据库不可用）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

#: 各问题类型的中文说明与处置建议（面向运维，直接打印）
_KIND_HELP = {
    "missing_file": (
        "DB 有记录、磁盘无文件",
        "笔记在界面上可见但内容缺失。可从备份恢复文件，或删除该笔记记录。",
    ),
    "orphan_file": (
        "磁盘有文件、DB 无记录",
        "占用空间且无法通过界面删除。确认无引用后可手工删除。",
    ),
    "hash_mismatch": (
        "文件内容与记录的哈希不符",
        "文件被截断或替换。建议从备份恢复。",
    ),
    "size_mismatch": (
        "文件大小与记录不符",
        "可能是截断。建议从备份恢复。",
    ),
}


async def _run(
    user_id: str | None, deep: bool, as_json: bool, include_orphans: bool,
) -> int:
    from app import database as db_mod
    from app.services.vault_audit_service import audit_vault

    await db_mod.init_db()
    async with db_mod.get_session_factory()() as session:
        result = await audit_vault(
            session, user_id=user_id, deep=deep, include_orphans=include_orphans,
        )

    if as_json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0 if result.ok else 1

    print("=" * 70)
    print("Vault 一致性校验")
    print("=" * 70)
    print(f"  模式        : {'深度（含 sha256）' if deep else '常规（按存在性）'}")
    print(f"  笔记扫描    : {result.notes_scanned}")
    print(f"  DB 记录对象 : {result.db_objects}")
    print(f"  磁盘对象    : {result.disk_objects}")

    if result.ok:
        print("\n  结论: 无不一致 —— DB 记录与磁盘文件完全对应")
        return 0

    print(f"\n  发现 {len(result.issues)} 处不一致：")
    for kind, count in sorted(result.counts.items()):
        label, advice = _KIND_HELP.get(kind, (kind, ""))
        print(f"\n  [{kind}] {count} 处 —— {label}")
        if advice:
            print(f"      处置: {advice}")

    print("\n  明细（最多 20 条）：")
    for issue in result.issues[:20]:
        note = issue.note_id[:8] if issue.note_id else "—"
        print(f"    - {issue.kind:<15} note={note}  {issue.object_name}")
        if issue.detail:
            print(f"      {issue.detail}")
    if len(result.issues) > 20:
        print(f"    … 其余 {len(result.issues) - 20} 条略（用 --json 取全部）")

    print("\n  提示：本脚本只报告，不修改任何数据。")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Vault 一致性校验（只报告，不修改）")
    parser.add_argument("--user", help="只校验指定用户 ID")
    parser.add_argument("--deep", action="store_true", help="逐文件比对 sha256（慢）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument(
        "--no-orphans", action="store_true",
        help="跳过孤儿扫描（只做 DB→磁盘方向，快很多）",
    )
    args = parser.parse_args()

    try:
        return asyncio.run(_run(
            args.user, args.deep, args.json, not args.no_orphans,
        ))
    except Exception as exc:
        print(f"校验失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
