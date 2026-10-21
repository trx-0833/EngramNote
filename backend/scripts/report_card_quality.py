# -*- coding: utf-8 -*-
"""
低分卡报告生成器（A4，**只读**）
=================================

用法：

```
python scripts/report_card_quality.py                        # 打印摘要
python scripts/report_card_quality.py --out ../docs/card-quality-report.md
python scripts/report_card_quality.py --top 80 --include-ok  # 列更多、连没命中的也列
```

## ⚠️ 这个脚本**只读**

它以 `mode=ro` 打开数据库，且 `app.services.card_quality_service` 里**没有**
任何写操作。`docs/agent-workstream-plan.md` §5 第 6 条已拍板：
**A4 只产出报告与标记，不删卡、不合并、不改复习历史**。

## 为什么用 sqlite3 直连而不是 ORM

与 `scripts/report_duplicate_cards.py` 同一条理由：这是**运维脚本**，
它要在"服务没起、依赖没装全"的情况下也能跑。而且 `mode=ro` 由 SQLite 自己
强制 —— 比"我保证不写"可靠。

## 重复标题那一节的数据从哪来

**不重新实现一遍**。直接调 `report_duplicate_cards.py` 里已有的
`load()` + `classify()`（同判据），否则两条报告会给出不一致的重复数 ——
而"两个工具说不一样的话"是这个仓库反复踩过的坑。
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.services.card_quality_service import build_report, render_markdown  # noqa: E402

DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "db", "engramnote.db"
)


def load_cards(db_path: str, user_id: str) -> list[dict]:
    """只读加载卡片（回收站里的笔记不算）"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        if user_id:
            uid = user_id
        else:
            row = con.execute(
                "SELECT user_id FROM knowledge_cards GROUP BY user_id "
                "ORDER BY count(*) DESC LIMIT 1"
            ).fetchone()
            if not row:
                return []
            uid = row[0]
        rows = con.execute(
            """
            SELECT c.id, c.title, c.content, c.card_type, c.note_id
            FROM knowledge_cards c
            LEFT JOIN notes n ON n.id = c.note_id
            WHERE c.user_id = ? AND (n.trashed_at IS NULL)
            """,
            (uid,),
        ).fetchall()
    finally:
        con.close()
    return [
        {"id": r[0], "title": r[1], "content": r[2], "card_type": r[3], "note_id": r[4]}
        for r in rows
    ]


def duplicate_summary(db_path: str, user_id: str) -> dict:
    """重复**标题**的汇总（复用 `report_duplicate_cards.py` 的同一套函数）

    为什么复用而不是重写：两条报告给出不一样的重复数时，
    "哪个对"会变成一个查不出结论的问题。同判据是唯一可靠的做法。
    """
    from scripts.report_duplicate_cards import classify, load

    try:
        cards = load(db_path, user_id)
        _label, ratio = classify(cards)
    except Exception as exc:  # noqa: BLE001 - 附带一节不该让整份报告失败
        return {"error": f"{type(exc).__name__}: {exc}", "title_groups": 0, "cards_involved": 0}

    by_title: dict[str, int] = {}
    for c in cards:
        by_title[c["title"]] = by_title.get(c["title"], 0) + 1
    dup_titles = {t: n for t, n in by_title.items() if n >= 2}
    return {
        "title_groups": len(dup_titles),
        "cards_involved": sum(dup_titles.values()),
        "duplicate_ratio": ratio,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="卡片质量筛查报告（只读）")
    ap.add_argument("--db", default=DEFAULT_DB, help="数据库路径（只读打开）")
    ap.add_argument("--user-id", default="", help="用户 ID（默认取卡片最多的那个）")
    ap.add_argument("--top", type=int, default=40, help="markdown 里列多少张")
    ap.add_argument("--include-ok", action="store_true", help="连没命中的卡也列出来")
    ap.add_argument("--out", default="", help="写成 markdown 到该路径")
    args = ap.parse_args()

    if not Path(args.db).exists():
        print(f"[FAIL] 库不存在: {args.db}")
        return 2

    cards = load_cards(args.db, args.user_id)
    if not cards:
        print("[FAIL] 没读到任何卡片（用户 ID 写错？库里没数据？）")
        return 2

    report = build_report(cards, only_suspicious=not args.include_ok)
    dup = duplicate_summary(args.db, args.user_id)

    print(f"卡片总数        : {report['total']}")
    print(f"命中至少一条规则: {report['flagged']}"
          f"（{report['flagged'] / report['total'] * 100:.1f}%）")
    print("按规则分布      :")
    for name, count in report["by_reason"].items():
        print(f"  {name:20s} {count}")
    print(f"重复标题种数    : {dup.get('title_groups', 0)}"
          f"（涉及 {dup.get('cards_involved', 0)} 张）")
    if dup.get("error"):
        print(f"  ⚠️ 重复统计失败: {dup['error']}")
    print()
    print("最可疑的 10 张（分数越低越可疑）:")
    for c in report["cards"][:10]:
        print(f"  [{c.score:3d}] {c.title[:28]!r:34s} {c.excerpt[:42]!r}")

    if args.out:
        md = render_markdown(report, top=args.top, duplicate_section=dup)
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md, encoding="utf-8")
        print(f"\n[OK] 已写入 {out_path}（{len(md)} 字符）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
