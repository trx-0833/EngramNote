# -*- coding: utf-8 -*-
"""A8 的追问语料 + A/B 验收（A9 追问子集）。

报告对 A8 的验收：「追问子集（20 题）Recall@5 相对基线 +5 点，否则回退」。

做法：
1. 从评测集的可答题里选 20 对 (q1, q2)：q2 改成**短追问**形态（"那它对寿命有影响吗？"
   这类），使单独检索必然低效；
2. 对每一对跑两次检索：
   - **基线**：`build_retrieval_query(prev, cur, None)`（只有上一问拼接）
   - **A8**：`build_retrieval_query(prev, cur, [anchor_title])`
3. 比较 Recall@K。

金标沿用 q2 的 gold chunk（原始问题的答案位置）。
"""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path
from typing import Any, Dict, List

GOLDENS = (Path(__file__).resolve().parents[1] / ".." / "research" / "experiments" / "20261116-qa-eval").resolve()

#: 短追问模板：本身几乎不含主题词，正是 A8 要解决的形态
_FOLLOWUPS = [
    "那它对寿命有影响吗？",
    "这条要求在什么情况下例外？",
    "具体数值是多少？",
    "如果达不到会怎样？",
    "谁负责这一项？",
    "多久检查一次？",
    "和上一节的要求冲突吗？",
    "这个规定的依据是什么？",
    "现场执行时最常见的问题是什么？",
    "有没有更简化的做法？",
]


def build_pairs(n: int = 20) -> List[Dict[str, Any]]:
    items = [
        json.loads(line)
        for line in (GOLDENS / "goldens.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    answerable = [it for it in items if it["kind"] == "answerable" and it.get("gold_chunk_id")]
    rng = random.Random(7)
    picked = rng.sample(answerable, min(len(answerable), n))
    pairs = []
    for i, it in enumerate(picked):
        pairs.append(
            {
                "id": f"f{i + 1:02d}",
                "prev_question": it["question"],
                "current_question": _FOLLOWUPS[i % len(_FOLLOWUPS)],
                "anchor_title": _title_of(it["question"]),
                "gold_chunk_id": it["gold_chunk_id"],
            }
        )
    out = GOLDENS / "followups.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for p in pairs:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")
    print(f"已生成 {len(pairs)} 条追问 → {out}")
    return pairs


def _title_of(question: str) -> str:
    """从可答题问题里取《...》的标题（我们的生成器就是这么造的）。"""
    if "《" in question and "》" in question:
        return question.split("《", 1)[1].split("》", 1)[0]
    return ""


async def run_ab(user_id: str | None = None) -> int:
    """跑 A/B：基线 vs A8，输出一张对比表。"""
    from sqlalchemy import select

    from app.config import get_settings
    from app.database import get_session_factory
    from app.models.user import User
    from app.services.qa_context import build_retrieval_query
    from app.services.rag_service import RAGService

    path = GOLDENS / "followups.jsonl"
    if not path.exists():
        print("还没有追问语料，先跑 --pairs")
        return 1
    pairs = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    factory = get_session_factory()
    async with factory() as db:
        if user_id:
            uid = user_id
        else:
            uid = (await db.execute(select(User).limit(1))).scalars().first().id
            print(f"⚠️ 未指定 --user-id，使用用户表第一行：{uid}")

    k = int(getattr(get_settings(), "rag_context_top_k", 8))
    rag = RAGService()

    rows = []
    for p in pairs:
        base_q = build_retrieval_query(p["prev_question"], p["current_question"], None)
        a8_q = build_retrieval_query(p["prev_question"], p["current_question"], [p["anchor_title"]])
        gold = p["gold_chunk_id"]

        base_hit = a8_hit = False
        for label, q in (("base", base_q), ("a8", a8_q)):
            r = await rag.retrieve_context(question=q, user_id=uid)
            got = {s.get("chunk_id") for s in (r.get("sources") or [])[:k]}
            hit = gold in got
            if label == "base":
                base_hit = hit
            else:
                a8_hit = hit
        rows.append({"id": p["id"], "base": base_hit, "a8": a8_hit})

    n = len(rows)
    base_rate = sum(r["base"] for r in rows) / n if n else 0
    a8_rate = sum(r["a8"] for r in rows) / n if n else 0
    delta = (a8_rate - base_rate) * 100

    print(f"追问子集 {n} 条 | K={k}")
    print(f"基线 Recall@{k} = {base_rate * 100:.1f}%")
    print(f"A8   Recall@{k} = {a8_rate * 100:.1f}%")
    print(f"Δ = {delta:+.1f} 点（报告要求 ≥ +5 点，否则回退）")
    print("判定:", "达标 ✅" if delta >= 5 else "未达标 ❌（按报告要求应回退 A8）")

    report = GOLDENS / "a8-ab.md"
    lines = [
        "# A8 追问子集 A/B 对比",
        "",
        f"- 语料：{n} 条短追问（{GOLDENS / 'followups.jsonl'}）",
        f"- K = {k}（`rag_context_top_k`）",
        "",
        "| 口径 | Recall@%d |" % k,
        "| --- | --- |",
        f"| 基线（不拼锚点） | {base_rate * 100:.1f}% |",
        f"| A8（拼笔记标题锚点） | {a8_rate * 100:.1f}% |",
        f"| Δ | **{delta:+.1f} 点** |",
        "",
        f"判定：{'达标（≥ +5 点）' if delta >= 5 else '**未达标** —— 按报告要求应回退 A8'}",
        "",
        "> 注意：若评测时向量检索整批跳过（维度不符 / worker 降级），本表两组都退化为 BM25，",
        "> Δ 不可信 —— 先看 `report.md` 顶部的环境警告。",
        "",
        "| id | 基线 | A8 |",
        "| --- | --- | --- |",
    ]
    for r in rows:
        lines.append(f"| {r['id']} | {'✅' if r['base'] else '—'} | {'✅' if r['a8'] else '—'} |")
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"报告 → {report}")
    return 0


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--user-id", default=None)
    a = ap.parse_args()
    if a.pairs:
        build_pairs()
        raise SystemExit(0)
    if a.run:
        raise SystemExit(asyncio.run(run_ab(a.user_id)))
    ap.print_help()
