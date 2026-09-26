#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""重复卡片只读报告 —— 给"要不要合并、怎么合并"提供依据

## 为什么需要它

关系判定的"同名闸"（`graph_service.auto_suggest_relations`）只做了**不建边**：
标题相同的卡片对不再被当成「相关」建议。但那**没有解决**重复问题 ——
实测语料里有 **42 种 / 96 张**标题重复的卡片，它们该被**合并**。

同名闸把这个问题从"藏在图谱里"挪到了"看得见的孤点 + 一条日志"。
本脚本把它变成**可以逐组决定的清单**。

## 它**不**做什么

**只读**，一行都不改。合并要动卡片本身的存续（属于"删数据/改产品语义"），
得先看清楚再决定。本脚本只负责把"看清楚"需要的信息摆出来。

## 怎么判"重复"

不依赖向量（真库此时可能还没有向量），用两样东西：

  1. **内容指纹**（`card_intake_service.card_content_hash`）—— 完全相同 = **确凿重复**，
     合并零风险
  2. **字符 bigram Jaccard**（标题 + 正文）—— 度量"像到什么程度"，
     用来区分"近乎逐字"与"同名但讲的是两回事"

⚠️ 第 2 项是**词法**度量，不是语义：`冷却水温度高处理` 与 `冷却水温高处理`
会很高，而 `范围定义` 与 `标准适用范围` 会很低 —— 这恰好是我们要的区分
（前者是同一件事的两种写法，后者是同模板的两份不同内容）。

## 用法

    python scripts/report_duplicate_cards.py                 # 打到 stdout
    python scripts/report_duplicate_cards.py --out dup.md    # 写成 markdown
    python scripts/report_duplicate_cards.py --min-count 3   # 只看出现 >=3 次的标题

退出码：0 = 跑完了；2 = **检查自身空转**（库读不到 / 没有任何标题重复）。
"""

import argparse
import os
import sqlite3
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "db", "engramnote.db"
)


def bigrams(text: str) -> set:
    """字符 bigram —— 中文短文本上比分词稳，且不需要词典"""
    t = "".join(ch for ch in text if ch.strip())
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) > 1 else {t}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def load(db_path: str, user_id: str) -> List[dict]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        if user_id:
            uid = user_id
        else:
            uid = con.execute(
                "SELECT user_id FROM knowledge_cards GROUP BY user_id "
                "ORDER BY count(*) DESC LIMIT 1"
            ).fetchone()[0]
        rows = con.execute(
            """
            SELECT c.id, c.title, c.content, c.content_hash, c.note_id, c.chapter_title,
                   n.title AS note_title
            FROM knowledge_cards c
            LEFT JOIN notes n ON n.id = c.note_id
            WHERE c.user_id = ? AND (n.trashed_at IS NULL)
            ORDER BY c.title
            """,
            (uid,),
        ).fetchall()
    finally:
        con.close()
    return [
        {
            "id": r[0], "title": (r[1] or "").strip(), "content": r[2] or "",
            "hash": r[3], "note_id": r[4], "chapter": r[5] or "", "note": r[6] or "",
        }
        for r in rows
    ]


def classify(cards: List[dict]) -> Tuple[str, float]:
    """一组同名卡片 → (类别, 两两相似度的最小值)

    类别：
      - `identical`：**每一对**的内容指纹都相同 → 确凿重复，合并零风险
      - `near`：两两相似度最小也 >= 0.80 → 近乎逐字，多半是重复
      - `mixed`：同名但内容明显不同 → 模板文本（如各标准的"适用范围"），**不该合并**
    """
    sims = [jaccard(bigrams(c["title"] + c["content"]), bigrams(d["title"] + d["content"]))
            for c, d in combinations(cards, 2)]
    lo = min(sims) if sims else 0.0
    hashes = [c["hash"] for c in cards]
    if all(h for h in hashes) and len(set(hashes)) == 1:
        return "identical", lo
    return ("near" if lo >= 0.80 else "mixed"), lo


def main() -> int:
    ap = argparse.ArgumentParser(description="重复卡片只读报告")
    ap.add_argument("--db", default=DEFAULT_DB, help="数据库路径（只读打开）")
    ap.add_argument("--user-id", default="", help="用户 ID（默认取卡片最多的那个）")
    ap.add_argument("--min-count", type=int, default=2, help="只看出现 >=N 次的标题")
    ap.add_argument("--out", default="", help="写成 markdown 到该路径")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"[ERROR] 库不存在：{args.db} —— 检查自身空转，退出码 2")
        return 2

    cards = load(args.db, args.user_id)
    groups: Dict[str, List[dict]] = defaultdict(list)
    for c in cards:
        if c["title"]:
            groups[c["title"]].append(c)
    dup = {t: v for t, v in groups.items() if len(v) >= args.min_count}

    print("=" * 76)
    print("重复卡片报告（**只读** —— 本脚本一行都不改）")
    print("=" * 76)
    print(f"  卡片 {len(cards)} 张 ｜ 不同标题 {len(groups)} 种 ｜ "
          f"标题出现 >={args.min_count} 次的 {len(dup)} 种，"
          f"涉及 {sum(len(v) for v in dup.values())} 张")
    if not dup:
        print("  没有任何标题重复 —— 检查自身空转，退出码 2")
        return 2

    rows = []
    for title, members in sorted(dup.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        kind, lo = classify(members)
        rows.append((title, members, kind, lo))

    by_kind = defaultdict(int)
    for _t, members, kind, _lo in rows:
        by_kind[kind] += len(members)
    print("\n  按性质分：")
    print(f"    确凿重复（内容指纹全同）  {by_kind['identical']:3d} 张"
          f"  ← 合并零风险")
    print(f"    近乎逐字（相似度 >=0.80） {by_kind['near']:3d} 张"
          f"  ← 多半是重复，建议人工确认")
    print(f"    同名异内容（模板文本）    {by_kind['mixed']:3d} 张"
          f"  ← **不该合并**（如各标准的「适用范围」）")

    lines = ["# 重复卡片报告（只读）", "",
             f"卡片 {len(cards)} 张 ｜ 标题重复 {len(dup)} 种 / "
             f"{sum(len(v) for v in dup.values())} 张", ""]
    for title, members, kind, lo in rows:
        label = {"identical": "确凿重复", "near": "近乎逐字", "mixed": "同名异内容"}[kind]
        lines += [f"## {title}（{len(members)} 张，{label}，最低相似度 {lo:.2f}）", ""]
        lines.append("| # | 卡片 id | 笔记 | 章节 | 正文开头 |")
        lines.append("|---|---|---|---|---|")
        for i, c in enumerate(members, 1):
            body = c["content"].strip().replace("\n", " ")[:40]
            lines.append(f"| {i} | `{c['id'][:8]}` | {c['note'][:18]} | "
                         f"{c['chapter'][:16] or '—'} | {body} |")
        lines.append("")

    if args.out:
        Path(args.out).write_text("\n".join(lines), encoding="utf-8")
        print(f"\n  报告已写入：{args.out}")

    print("\n" + "=" * 76)
    print("下一步（**都要你拍板**，本脚本不代做）")
    print("=" * 76)
    print("  1. 「确凿重复」可以直接合并 —— 内容逐字相同，合并零信息损失")
    print("  2. 「近乎逐字」需要人工看 —— 有些是同一件事的两种写法，")
    print("     有些是不同设备的同名参数（如两台机组的「发电机型号」）")
    print("  3. 「同名异内容」**不要合并** —— 它们是模板文本，各自属于不同的标准")
    return 0


if __name__ == "__main__":
    sys.exit(main())
