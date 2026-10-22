# -*- coding: utf-8 -*-
"""P2：**逐篇跑完整三元组**（v1+v2+v3 同一篇连着跑）

## 为什么不是「版本在外层」

驱动器的循环是 `for version: for note:` —— 先跑完所有 v1，再跑 v2。
在时间受限的窗口里这很糟：中途没时间就只剩**半份 v2**，什么都结论不了。

改成**逐篇**：每完成一篇就多一组**完整**的三版对照。中途停下时，
已完成的部分**每一篇都是可用的**。

## 顺序（省时间的排序）

1. **已跑过 v1 但缺 v2/v3 的笔记** —— 理解命中缓存，只需重出题，最便宜；
2. 其余笔记 —— 理解要重跑（贵），按卡片数升序（小的先做，多完成几篇）。

## 用法

    python scripts/run_triples.py --tmp data/_uxwalk --minutes 90
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time

BACKEND = pathlib.Path(__file__).resolve().parents[1]
RERUN = BACKEND / "scripts" / "rerun_understanding.py"
SNAP = BACKEND / "data" / "_uxwalk" / "rerun"


def load_done() -> dict:
    """`{note_id: {version, ...}}` —— 从快照读已完成的轮次"""
    done: dict = {}
    for v in ("1", "2", "3"):
        f = SNAP / f"snapshot-v{v}.json"
        if not f.exists():
            continue
        for r in json.loads(f.read_text(encoding="utf-8")).get("runs", []):
            if "error" in r:
                continue
            done.setdefault(r["note_id"], set()).add(str(r.get("version")))
    return done


def load_notes() -> list[dict]:
    import sqlite3

    db = BACKEND / "data" / "_uxwalk" / "db" / "engram.db"
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """SELECT n.id, n.title,
                      (SELECT count(*) FROM knowledge_cards k WHERE k.note_id = n.id) AS cards
               FROM notes n WHERE n.trashed_at IS NULL ORDER BY cards ASC"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="逐篇跑完整三版对照")
    ap.add_argument("--tmp", default="data/_uxwalk")
    ap.add_argument("--minutes", type=float, default=90, help="时间预算（分钟）")
    args = ap.parse_args()

    deadline = time.monotonic() + args.minutes * 60
    done = load_done()
    notes = load_notes()

    # 排序：先补「已跑过 v1 但缺 v2/v3」的（理解命中缓存，最便宜）
    def priority(n: dict) -> tuple:
        have = done.get(n["id"], set())
        cheap = 0 if ("1" in have and not {"2", "3"} <= have) else 1
        return (cheap, n["cards"])

    pending = [n for n in notes if not {"1", "2", "3"} <= done.get(n["id"], set())]
    pending.sort(key=priority)

    print(f"[triples] 待做 {len(pending)} 篇，预算 {args.minutes:.0f} 分钟")
    for n in pending[:12]:
        have = sorted(done.get(n["id"], set()))
        print(f"   {n['cards']:4d} 卡  已有 v{','.join(have) or '—':6s} {n['title'][:34]}")

    completed = 0
    for i, n in enumerate(pending, 1):
        if time.monotonic() >= deadline:
            print(f"[triples] 时间到，停在 {i-1}/{len(pending)}")
            break
        have = done.get(n["id"], set())
        need = [v for v in ("1", "2", "3") if v not in have]
        if not need:
            continue
        remain = (deadline - time.monotonic()) / 60
        print(
            f"[triples] {i}/{len(pending)} 剩 {remain:.0f} 分钟 | {n['title'][:30]} "
            f"（{n['cards']} 卡）补 v{','.join(need)}",
            flush=True,
        )
        t0 = time.monotonic()
        subprocess.run(
            [
                sys.executable,
                str(RERUN),
                "--tmp",
                args.tmp,
                "--note-id",
                n["id"],
                "--versions",
                ",".join(need),
            ],
            cwd=str(BACKEND),
            check=False,
        )
        took = (time.monotonic() - t0) / 60
        print(f"[triples]   用时 {took:.1f} 分钟", flush=True)
        completed += 1

    print(f"[triples] 完成 {completed} 篇")
    return 0


if __name__ == "__main__":
    sys.exit(main())
