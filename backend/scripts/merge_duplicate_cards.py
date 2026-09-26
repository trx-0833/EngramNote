#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""合并重复卡片 —— **同标题 + 内容近乎逐字**的卡片并成一张

================================================================================
为什么需要它
================================================================================

关系判定的"同名闸"（`graph_service.auto_suggest_relations`）只做了**不建边**：
标题相同的卡片对不再被当成「相关」建议。但那没有解决重复问题 ——
实测语料里有 **42 组 / 96 张**标题重复的卡片。它们被分成三类
（`scripts/report_duplicate_cards.py`）：

    确凿重复（内容指纹全同）    6 张  ← 合并零风险
    近乎逐字（相似度 >=0.80）  18 张  ← 同一件事的两种措辞
    同名异内容（模板文本）     72 张  ← **不该合并**（各标准的"适用范围"等）

本脚本只合并**前两类**，第三类一律不碰。

================================================================================
它会动哪些数据（合并 = 把引用改指到存活者，再删掉多余的那张）
================================================================================

按 schema 实测（`PRAGMA` 查出来的，不是翻模型猜的），引用 `knowledge_cards` 的有 5 处：

    card_relations.card_id_1 / card_id_2   ON DELETE SET NULL
    quiz_items.card_id                     无 ON DELETE（不处理会留下悬空引用）
    review_logs.card_id                    可空
    review_states.item_id（item_type='card'）
    knowledge_cards.parent_card_id         自引用（实测 0 处）

**三条硬约束会让"直接改指"失败，必须显式处理**：

  1. `ck_card_relation_no_self_ref`：两张卡**彼此有关系**时，改指后变成自环 → 删掉那条
  2. `uq_card_relations_pair_type` (user, c1, c2, type)：两张卡有**共同邻居**时，
     改指后出现重复对 → 保留一条（confirmed 优先，再看相似度）
  3. `sqlite_autoindex_review_states_2` (user, item_type, item_id)：两张卡都有复习状态时，
     改指会撞唯一索引 → **保留存活者的**（它的学习进度更值得留）

================================================================================
安全设计
================================================================================

- **默认 dry-run**：不写库，只打印"会做什么、动多少行"
- `--apply` 才真写，且**整个合并在一个事务里**（中途失败全回滚）
- `--backup` 先复制一份库（默认开；`--no-backup` 关掉）
- 写完**自检**：悬空引用、自环、重复对，任何一项不为 0 就回滚
- 只合并 `--min-similarity`（默认 0.80）以上的组；同名异内容自动排除

用法：

    python scripts/merge_duplicate_cards.py                     # dry-run（默认）
    python scripts/merge_duplicate_cards.py --min-similarity 1.0 # 只合并指纹全同的
    python scripts/merge_duplicate_cards.py --apply             # 真做（先自动备份）

退出码：0 = 跑完了；1 = 执行失败；2 = **检查自身空转**（库读不到 / 没有可合并的组）。
"""

import argparse
import os
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
from typing import Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "db", "engramnote.db"
)


def bigrams(text: str) -> set:
    t = "".join(ch for ch in text if ch.strip())
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) > 1 else {t}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def similarity(x: dict, y: dict) -> float:
    return jaccard(bigrams(x["title"] + x["content"]), bigrams(y["title"] + y["content"]))


# ============================================================================
# 计划：算出"哪些组合并、谁活下来"
# ============================================================================


def load_cards(con: sqlite3.Connection, user_id: str) -> List[dict]:
    rows = con.execute(
        """SELECT c.id, c.title, c.content, c.content_hash, c.note_id, c.created_at
           FROM knowledge_cards c LEFT JOIN notes n ON n.id = c.note_id
           WHERE c.user_id = ? AND (n.trashed_at IS NULL)""",
        (user_id,),
    ).fetchall()
    return [
        {"id": r[0], "title": (r[1] or "").strip(), "content": r[2] or "",
         "hash": r[3], "note_id": r[4], "created_at": r[5] or ""}
        for r in rows
    ]


def ref_counts(con: sqlite3.Connection, ids: List[str]) -> Dict[str, int]:
    """每张卡被引用了多少次 —— 存活者选"引用最多"的那张（信息损失最小）"""
    if not ids:
        return {}
    q = ",".join("?" * len(ids))
    out: Dict[str, int] = defaultdict(int)
    for sql in (
        f"SELECT card_id, count(*) FROM quiz_items WHERE card_id IN ({q}) GROUP BY 1",
        f"SELECT card_id, count(*) FROM review_logs WHERE card_id IN ({q}) GROUP BY 1",
        f"SELECT card_id_1, count(*) FROM card_relations WHERE card_id_1 IN ({q}) GROUP BY 1",
        f"SELECT card_id_2, count(*) FROM card_relations WHERE card_id_2 IN ({q}) GROUP BY 1",
    ):
        for cid, n in con.execute(sql, ids):
            out[cid] += n
    return out


def build_plan(con: sqlite3.Connection, user_id: str, min_sim: float) -> List[dict]:
    cards = load_cards(con, user_id)
    by_title: Dict[str, List[dict]] = defaultdict(list)
    for c in cards:
        if c["title"]:
            by_title[c["title"]].append(c)

    groups = []
    for title, members in by_title.items():
        if len(members) < 2:
            continue
        sims = [similarity(a, b) for a, b in combinations(members, 2)]
        if min(sims) < min_sim:
            continue          # 同名异内容 → 不是重复，跳过
        counts = ref_counts(con, [m["id"] for m in members])
        # 存活者：引用最多 → 创建最早（最早的那张更可能是原始抽取）
        members_sorted = sorted(members, key=lambda m: (-counts.get(m["id"], 0), m["created_at"]))
        groups.append({
            "title": title,
            "keep": members_sorted[0],
            "drop": members_sorted[1:],
            "min_sim": min(sims),
            "all_same_hash": bool(members_sorted[0]["hash"])
            and len({m["hash"] for m in members}) == 1,
            "refs": {m["id"]: counts.get(m["id"], 0) for m in members},
        })
    return groups


# ============================================================================
# 执行
# ============================================================================


def _merge_one(con: sqlite3.Connection, keep: str, drop: str) -> Dict[str, int]:
    """把 `drop` 的所有引用改指到 `keep`，处理三条硬约束，然后删掉 `drop`"""
    stats = defaultdict(int)

    # ── ① card_relations ──
    rels = con.execute(
        """SELECT id, card_id_1, card_id_2, relation_type, status, similarity_score
           FROM card_relations WHERE card_id_1 IN (?, ?) OR card_id_2 IN (?, ?)""",
        (keep, drop, keep, drop),
    ).fetchall()
    for rid, c1, c2, rtype, status, score in rels:
        n1 = keep if c1 == drop else c1
        n2 = keep if c2 == drop else c2
        if n1 == n2:
            # 约束 1：两张卡彼此有关系 → 改指后是自环，删掉
            con.execute("DELETE FROM card_relations WHERE id = ?", (rid,))
            stats["关系_自环删除"] += 1
            continue
        if c1 == n1 and c2 == n2:
            continue                      # 本来就不涉及 drop
        # 约束 2：改指后可能与已有的对重复
        dup = con.execute(
            """SELECT id, status, similarity_score FROM card_relations
               WHERE user_id = (SELECT user_id FROM card_relations WHERE id = ?)
                 AND card_id_1 = ? AND card_id_2 = ? AND relation_type = ? AND id != ?""",
            (rid, n1, n2, rtype, rid),
        ).fetchone()
        if dup:
            # 保留"更可信"的那条：confirmed > suggested > rejected，再看相似度
            rank = {"confirmed": 2, "suggested": 1, "rejected": 0}
            if (rank.get(status, 0), score or 0) > (rank.get(dup[1], 0), dup[2] or 0):
                con.execute("DELETE FROM card_relations WHERE id = ?", (dup[0],))
                con.execute(
                    "UPDATE card_relations SET card_id_1 = ?, card_id_2 = ? WHERE id = ?",
                    (n1, n2, rid),
                )
            else:
                con.execute("DELETE FROM card_relations WHERE id = ?", (rid,))
            stats["关系_重复对去重"] += 1
        else:
            con.execute(
                "UPDATE card_relations SET card_id_1 = ?, card_id_2 = ? WHERE id = ?",
                (n1, n2, rid),
            )
            stats["关系_改指"] += 1

    # ── ② quiz_items（无唯一约束，直接改指）──
    cur = con.execute("UPDATE quiz_items SET card_id = ? WHERE card_id = ?", (keep, drop))
    stats["题目_改指"] += cur.rowcount

    # ── ③ review_logs ──
    cur = con.execute("UPDATE review_logs SET card_id = ? WHERE card_id = ?", (keep, drop))
    stats["复习日志_改指"] += cur.rowcount

    # ── ④ review_states（约束 3：唯一索引 (user, item_type, item_id)）──
    keep_state = con.execute(
        "SELECT id FROM review_states WHERE item_type='card' AND item_id = ?", (keep,)
    ).fetchone()
    if keep_state:
        cur = con.execute(
            "DELETE FROM review_states WHERE item_type='card' AND item_id = ?", (drop,)
        )
        stats["复习状态_丢弃重复的"] += cur.rowcount
    else:
        cur = con.execute(
            "UPDATE review_states SET item_id = ? WHERE item_type='card' AND item_id = ?",
            (keep, drop),
        )
        stats["复习状态_改指"] += cur.rowcount

    # ── ⑤ parent_card_id ──
    cur = con.execute(
        "UPDATE knowledge_cards SET parent_card_id = ? WHERE parent_card_id = ?", (keep, drop)
    )
    stats["父卡_改指"] += cur.rowcount

    # ── ⑥ 删掉被合并的那张 ──
    con.execute("DELETE FROM knowledge_cards WHERE id = ?", (drop,))
    stats["卡片_删除"] += 1
    return dict(stats)


def self_check(con: sqlite3.Connection) -> List[str]:
    """合并后自检 —— 任何一项不为 0 就回滚"""
    problems = []
    n = con.execute(
        """SELECT count(*) FROM card_relations r
           WHERE NOT EXISTS (SELECT 1 FROM knowledge_cards c WHERE c.id = r.card_id_1)
              OR NOT EXISTS (SELECT 1 FROM knowledge_cards c WHERE c.id = r.card_id_2)"""
    ).fetchone()[0]
    if n:
        problems.append(f"card_relations 悬空引用 {n} 条")
    n = con.execute("SELECT count(*) FROM card_relations WHERE card_id_1 = card_id_2").fetchone()[0]
    if n:
        problems.append(f"card_relations 自环 {n} 条")
    n = con.execute(
        """SELECT count(*) FROM (SELECT user_id, card_id_1, card_id_2, relation_type
             FROM card_relations GROUP BY 1,2,3,4 HAVING count(*) > 1)"""
    ).fetchone()[0]
    if n:
        problems.append(f"card_relations 重复对 {n} 组")
    n = con.execute(
        """SELECT count(*) FROM quiz_items q
           WHERE NOT EXISTS (SELECT 1 FROM knowledge_cards c WHERE c.id = q.card_id)"""
    ).fetchone()[0]
    if n:
        problems.append(f"quiz_items 悬空引用 {n} 条")
    n = con.execute(
        """SELECT count(*) FROM review_states s WHERE s.item_type='card'
           AND NOT EXISTS (SELECT 1 FROM knowledge_cards c WHERE c.id = s.item_id)"""
    ).fetchone()[0]
    if n:
        problems.append(f"review_states(card) 悬空引用 {n} 条")
    n = con.execute(
        """SELECT count(*) FROM review_logs l WHERE l.card_id IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM knowledge_cards c WHERE c.id = l.card_id)"""
    ).fetchone()[0]
    if n:
        problems.append(f"review_logs 悬空引用 {n} 条")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="合并重复卡片（默认 dry-run）")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--user-id", default="")
    ap.add_argument("--min-similarity", type=float, default=0.80,
                    help="同组内容相似度下限（默认 0.80；1.0 = 只合并指纹全同的）")
    ap.add_argument("--apply", action="store_true", help="真写库（默认只打印计划）")
    ap.add_argument("--no-backup", action="store_true", help="不自动备份（不推荐）")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"[ERROR] 库不存在：{args.db} —— 检查自身空转，退出码 2")
        return 2

    con = sqlite3.connect(args.db)
    con.execute("PRAGMA foreign_keys = ON")
    try:
        uid = args.user_id or con.execute(
            "SELECT user_id FROM knowledge_cards GROUP BY user_id "
            "ORDER BY count(*) DESC LIMIT 1"
        ).fetchone()[0]
        plan = build_plan(con, uid, args.min_similarity)

        print("=" * 78)
        print(f"合并计划（{'真做' if args.apply else '**dry-run，不写库**'}）")
        print("=" * 78)
        total_cards = con.execute(
            "SELECT count(*) FROM knowledge_cards WHERE user_id = ?", (uid,)
        ).fetchone()[0]
        print(f"  用户 {uid[:8]} ｜ 卡片 {total_cards} 张 ｜ "
              f"可合并 {len(plan)} 组（相似度 >= {args.min_similarity}）")
        if not plan:
            print("  没有可合并的组 —— 检查自身空转，退出码 2")
            return 2

        for g in plan:
            kind = "指纹全同" if g["all_same_hash"] else f"相似度 {g['min_sim']:.2f}"
            print(f"\n  【{g['title'][:30]}】（{kind}）")
            print(f"     保留 {g['keep']['id'][:8]}（引用 {g['refs'][g['keep']['id']]} 次）")
            for d in g["drop"]:
                print(f"     合并 {d['id'][:8]}（引用 {g['refs'][d['id']]} 次）→ 删除")

        if not args.apply:
            print("\n" + "=" * 78)
            print("dry-run 结束 —— 加 --apply 才真做（会自动备份）")
            print("=" * 78)
            return 0

        # ── 备份 ──
        if not args.no_backup:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            bak = f"{args.db}.bak-merge-{stamp}"
            con.close()
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(args.db + suffix):
                    shutil.copy2(args.db + suffix, bak + suffix)
            print(f"\n  已备份：{bak}")
            con = sqlite3.connect(args.db)
            con.execute("PRAGMA foreign_keys = ON")

        # ── 执行（一个事务）──
        print("\n" + "=" * 78)
        print("执行")
        print("=" * 78)
        totals = defaultdict(int)
        try:
            con.execute("BEGIN")
            for g in plan:
                for d in g["drop"]:
                    for k, v in _merge_one(con, g["keep"]["id"], d["id"]).items():
                        totals[k] += v
            problems = self_check(con)
            if problems:
                con.execute("ROLLBACK")
                print("  [FAIL] 自检不通过，已回滚：")
                for p in problems:
                    print(f"    - {p}")
                return 1
            con.execute("COMMIT")
        except Exception as exc:  # noqa: BLE001
            con.execute("ROLLBACK")
            print(f"  [FAIL] {exc} —— 已回滚")
            return 1

        for k, v in sorted(totals.items()):
            print(f"  {k:20s} {v}")
        print("\n  自检通过（无悬空引用 / 无自环 / 无重复对）")
        left = con.execute(
            "SELECT count(*) FROM knowledge_cards WHERE user_id = ?", (uid,)
        ).fetchone()[0]
        print(f"  卡片 {total_cards} → {left}（合并掉 {total_cards - left} 张）")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
