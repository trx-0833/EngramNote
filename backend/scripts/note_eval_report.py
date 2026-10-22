# -*- coding: utf-8 -*-
"""B5 ②：AI 对比原始资料，指出**遗漏概念**与**理解偏差**（只读真库）

## 它补的是什么视角

A4 判「这张卡自己好不好」（不需要原文）；B5 判「这批卡相对**这篇原文**缺了什么」
（必须两样都有）。用户面对的是产物，看不到"原文里还有什么没被抽出来"。

## 只读保证

- 数据库以 `mode=ro` 打开（只读连接）；
- 笔记正文通过对象存储读取，**不写任何东西**；
- 本脚本不执行 UPDATE/DELETE。

## 幂等靠 `llm_cache`

缓存键含提示词全文，而提示词含卡片内容与原文片段 ⇒ 卡片没变则第二次请求
**零额度**。所以重复跑同一篇是安全的（也是为什么可以放心小步试）。

## 用法

    python scripts/note_eval_report.py --limit 5            # 先小样
    python scripts/note_eval_report.py --out ../docs/note-eval-report-2026-10-22.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

DEFAULT_DB = str(BACKEND / "data" / "db" / "engramnote.db")


def load_notes(db_path: str, user_id: str, limit: int) -> List[Dict[str, Any]]:
    """只读加载笔记（含正文路径与卡片数），按卡片数**降序**（先看内容多的）"""
    con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        if not user_id:
            user_id = con.execute(
                "SELECT user_id FROM notes GROUP BY user_id ORDER BY count(*) DESC LIMIT 1"
            ).fetchone()[0]
        sql = """
            SELECT n.id, n.title, n.original_md_path, n.clean_md_path, n.status,
                   (SELECT count(*) FROM knowledge_cards k WHERE k.note_id = n.id) AS cards
            FROM notes n
            WHERE n.user_id = ? AND n.trashed_at IS NULL
              AND n.status IN ('archived', 'cleaned')
            ORDER BY cards DESC
        """
        rows = [dict(r) for r in con.execute(sql, (user_id,)).fetchall()]
        return rows[:limit] if limit else rows
    finally:
        con.close()


def load_cards(db_path: str, note_id: str) -> List[Dict[str, Any]]:
    con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return [
            dict(r)
            for r in con.execute(
                "SELECT id, title, content, card_type FROM knowledge_cards "
                "WHERE note_id = ? ORDER BY id",
                (note_id,),
            ).fetchall()
        ]
    finally:
        con.close()


def read_source(note: Dict[str, Any]) -> tuple[str | None, str]:
    """读笔记正文（clean 优先，回落 original）。返回 (文本, 说明)"""
    from app.config import get_settings
    from app.services.storage_service import get_object_bytes

    s = get_settings()
    for label, key in (("clean", note.get("clean_md_path")), ("original", note.get("original_md_path"))):
        if not key:
            continue
        try:
            data = get_object_bytes(s.minio_bucket_markdown, key)
            text = data.decode("utf-8")
            if text.strip():
                return text, label
        except Exception as e:  # noqa: BLE001
            print(f"    读 {label} 失败：{type(e).__name__}: {e}", flush=True)
    return None, "无"


def render_markdown(results: List[Dict[str, Any]], usage: Dict[str, Any]) -> str:
    lines = [
        "# B5 ② · AI 复核报告（遗漏概念 / 理解偏差）",
        "",
        "> **状态**：2026-10-22 生成。B5 ② 的产出。",
        "> **只读**：数据库以 `mode=ro` 打开，本报告不改任何卡片。",
        "> **视角**：A4 判「这张卡自己好不好」（不需要原文）；本报告判",
        "> 「这批卡相对**这篇原文**缺了什么」（必须两样都有）。",
        f"> **额度**：{usage.get('calls', 0)} 次调用 / {usage.get('tokens', 0):,} tokens。",
        "",
        "## 1. 汇总",
        "",
        "| 笔记 | 卡片 | 正文来源 | 分段 | 遗漏概念 | 理解偏差 | 调用 | tokens |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if r.get("error"):
            lines.append(f"| {r['title'][:26]} | {r.get('cards', 0)} | — | — | — | — | — | — |")
            continue
        lines.append(
            f"| {r['title'][:26]} | {r['cards']} | {r['source']} | {r['segments']} | "
            f"**{len(r['missing'])}** | **{len(r['misunderstandings'])}** | {r['calls']} | {r['tokens']:,} |"
        )

    tmc: Counter = Counter()
    for r in results:
        for m in r.get("missing", []):
            tmc[m["name"]] += 1
    lines += [
        "",
        f"**合计**：遗漏概念 {sum(len(r.get('missing', [])) for r in results)} 条，"
        f"理解偏差 {sum(len(r.get('misunderstandings', [])) for r in results)} 条。",
        "",
    ]

    for r in results:
        lines += [f"## {r['title']}", ""]
        if r.get("error"):
            lines += [f"**失败**：{r['error']}", ""]
            continue
        if r.get("source_unavailable"):
            lines += ["**无法复核**：这篇笔记读不到原文（没有可对照的东西）。", ""]
            continue
        if r.get("coverage_note"):
            lines += [f"> 整体覆盖判断：{r['coverage_note']}", ""]
        if r["missing"]:
            lines += [
                "### 遗漏的概念",
                "",
                "| 概念 | 为什么重要 | 原文依据 |",
                "|---|---|---|",
            ]
            for m in r["missing"]:
                lines.append(
                    f"| {m['name']} | {m['why_important'][:60]} | {m['source_quote'][:70]} |"
                )
            lines.append("")
        else:
            lines += ["### 遗漏的概念", "", "**未发现明显遗漏**（本段覆盖良好）。", ""]
        if r["misunderstandings"]:
            lines += [
                "### 理解偏差",
                "",
                "| 卡片 | 问题 | 原文依据 | 建议 |",
                "|---|---|---|---|",
            ]
            for m in r["misunderstandings"]:
                lines.append(
                    f"| {m['card_title'][:26] or m['card_id'][:8]} | {m['problem'][:60]} | "
                    f"{m['source_quote'][:60]} | {m['suggestion'][:50]} |"
                )
            lines.append("")
        else:
            lines += ["### 理解偏差", "", "**未发现明显偏差**。", ""]

    lines += [
        "## 明确不做的事",
        "",
        "- **不改任何卡片**：只产出清单。改卡是用户看着清单自己决定的事。",
        "- **不自动重跑理解**。",
        "- 报告里的「遗漏」是**模型判断**，不是客观事实 —— 它可能把原文的举例当成概念。",
        "  每条都给了 `原文依据`，**人可以据此否掉误判**。",
        "",
    ]
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> int:
    from app.services.llm.gateway import LLMGateway
    from app.services.note_eval_service import evaluate_note

    notes = load_notes(args.db, args.user_id, args.limit)
    if not notes:
        print("[b5] 没有可复核的笔记", file=sys.stderr)
        return 2
    print(f"[b5] 加载 {len(notes)} 篇笔记（只读）", flush=True)

    gateway = LLMGateway.from_settings()
    results: List[Dict[str, Any]] = []
    usage = {"calls": 0, "tokens": 0}

    for i, note in enumerate(notes, 1):
        cards = load_cards(args.db, note["id"])
        source, label = read_source(note)
        print(
            f"[b5] {i}/{len(notes)} {note['title'][:30]}  卡片={len(cards)} 正文={label}",
            flush=True,
        )
        try:
            res = await evaluate_note(source_text=source, cards=cards, gateway=gateway)
        except Exception as e:  # noqa: BLE001
            results.append({"title": note["title"], "cards": len(cards), "error": str(e)})
            continue
        usage["calls"] += res.calls
        usage["tokens"] += res.total_tokens
        results.append(
            {
                "title": note["title"],
                "cards": len(cards),
                "source": label,
                "segments": res.segments,
                "source_unavailable": res.source_unavailable,
                "coverage_note": res.coverage_note,
                "missing": [
                    {"name": m.name, "why_important": m.why_important, "source_quote": m.source_quote}
                    for m in res.missing_concepts
                ],
                "misunderstandings": [
                    {
                        "card_id": m.card_id,
                        "card_title": m.card_title,
                        "problem": m.problem,
                        "source_quote": m.source_quote,
                        "suggestion": m.suggestion,
                    }
                    for m in res.misunderstandings
                ],
                "calls": res.calls,
                "tokens": res.total_tokens,
            }
        )
        print(
            f"      遗漏 {len(res.missing_concepts)} / 偏差 {len(res.misunderstandings)}"
            f"  （调用 {res.calls}，{res.total_tokens:,} tokens）",
            flush=True,
        )
        # 增量落盘：中断不丢
        if args.json:
            Path(args.json).write_text(
                json.dumps({"results": results, "usage": usage}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    md = render_markdown(results, usage)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"[b5] 报告已写入 {args.out}")
    if args.json:
        Path(args.json).write_text(
            json.dumps({"results": results, "usage": usage}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(f"[b5] 合计 {usage['calls']} 次调用 / {usage['tokens']:,} tokens")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="B5 ② AI 复核（遗漏概念 / 理解偏差，只读）")
    ap.add_argument("--db", default=DEFAULT_DB, help="数据库路径（**只读**打开）")
    ap.add_argument("--user-id", default="", help="用户 ID（默认取笔记最多的那个）")
    ap.add_argument("--limit", type=int, default=0, help="只复核前 N 篇（按卡片数降序，0=全部）")
    ap.add_argument("--out", default="", help="markdown 输出路径")
    ap.add_argument("--json", default="", help="JSON 输出路径（增量落盘）")
    args = ap.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
