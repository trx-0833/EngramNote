"""QA 评测骨架（A9，`research/topics/qa-gap-triage.md`）。

报告要求：从现有笔记出 60 题（40 可答 / 10 不可答 / 10 多跳），每题标 gold chunk_id；
脚本跑三件事：**检索命中**、**引用覆盖**、**拒答是否正确**。

## 为什么"骨架"阶段不调 LLM

`faithfulness` 的正规做法要 LLM 判分（每轮一次付费调用）。骨架先跑**检索层与拒答层**
——这两层零成本、可重复、且正是 A8 要验收的东西。`--with-answers` 目前未实装（见下）。

## round7 审计改掉的四个会让数字失真的错

1. **可答题的问题里含 gold 前 40 字原文** ⇒ Recall 虚高（"答案就在题面里"）。
   现在问题**只用标题/章节名**造句，不含正文；
2. **10 道多跳题是同一问句** ⇒ 只能测一个点。
   现在按模板轮换；且两处 gold **必须来自不同笔记**（原来 10/10 同篇）；
3. **"Recall@5" 实为 Recall@8**（`rag_context_top_k=8`）⇒ 指标名与口径不符。
   现在 K 取**实际返回的上下文条数上限**，报告里写 `Recall@{K}`；
4. **`--limit N` 实际只跑多跳题**。现在取前 N 题。

## 产物去哪

- 评测集与报告：`research/experiments/<date>-qa-eval/`（`research/` 整目录 gitignore，
  符合 AGENTS §7「调研产物不入库」）；
- 结论：由人写进 `docs/`。

## 用法

```bash
python scripts/eval_qa.py --generate          # 从库里抽 60 题骨架
python scripts/eval_qa.py --run               # 跑检索层 + 拒答层，写报告
python scripts/eval_qa.py --run --limit 20    # 只跑前 20 题
```
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

GOLDENS = (Path(__file__).resolve().parents[1] / ".." / "research" / "experiments" / "20261116-qa-eval").resolve()

#: 多跳题的问句模板（轮换使用，避免 10 道题是同一句）
_MULTI_TEMPLATES = [
    "这两处规定在执行顺序上有什么不同？",
    "它们对同一项指标的要求一致吗？为什么？",
    "如果按这两份资料分别执行，哪一步会冲突？",
    "这两份材料各自强调了什么风险？",
    "把这两处要求放在一起看，有没有重复或矛盾的地方？",
    "这两套流程的启动条件分别是什么？",
    "它们对人员资质的要求有什么差别？",
    "这两处规定的时间间隔是怎么定的？",
    "如果要同时满足这两份资料，优先做哪一步？",
    "这两份资料的适用范围有重叠吗？",
]


def _goldens_file() -> Path:
    return GOLDENS / "goldens.jsonl"


def _report_file() -> Path:
    return GOLDENS / "report.md"


async def generate(count_answerable: int = 40, count_unanswerable: int = 10, count_multi: int = 10) -> int:
    """从库里抽题。

    ⚠️ 可答题的问题**只用标题/章节名**造句 —— 一旦把 chunk 正文片段拼进问题，
    题目本身就含答案，Recall 会被虚高地测出来（round7 audit）。
    """
    from sqlalchemy import select

    from app.database import get_session_factory
    from app.models.chunk import Chunk
    from app.models.note import Note

    GOLDENS.mkdir(parents=True, exist_ok=True)
    factory = get_session_factory()
    async with factory() as db:
        rows = (
            await db.execute(
                select(Chunk.id, Chunk.note_id, Chunk.heading_path, Note.title)
                .join(Note, Note.id == Chunk.note_id)
                .where(
                    Chunk.has_embedding.is_(True),
                    Note.trashed_at.is_(None),
                    Chunk.char_count >= 120,
                )
                .limit(4000)
            )
        ).all()

    if not rows:
        print("库里没有可用的 chunk —— 先跑 embed_chunks.py")
        return 1

    rng = random.Random(20261116)
    picked = rng.sample(rows, min(len(rows), count_answerable * 3))

    items: List[Dict[str, Any]] = []
    per_note: Counter[str] = Counter()
    for chunk_id, note_id, heading, note_title in picked:
        if len(items) >= count_answerable:
            break
        if per_note[note_id] >= 2:  # round7：原来是 list(...).count(...)，判错了
            continue
        per_note[note_id] += 1
        topic = (heading or "").split(">")[-1].strip() or (note_title or "").strip()
        # 只用主题词提问，**不带正文**（正文含 gold 原文会让 Recall 虚高）
        items.append(
            {
                "id": f"a{len(items) + 1:02d}",
                "kind": "answerable",
                "question": f"《{note_title}》里关于「{topic}」的具体要求是什么？",
                "gold_note_id": note_id,
                "gold_chunk_id": chunk_id,
                "should_refuse": False,
            }
        )

    unanswerable = [
        ("u01", "量子色动力学里的渐近自由是怎么推导出来的？"),
        ("u02", "拜占庭帝国查士丁尼法典的编纂时间是？"),
        ("u03", "《百年孤独》里蕾梅黛丝升天的象征意义是什么？"),
        ("u04", "TCP 拥塞控制里 BBR 算法的收敛性证明？"),
        ("u05", "宋代汝窑天青釉的呈色机理是？"),
        ("u06", "如何用 Coq 证明四色定理？"),
        ("u07", "蛋白质 AlphaFold3 的注意力机制细节？"),
        ("u08", "南美洲安第斯山脉的板块构造演化史？"),
        ("u09", "《红楼梦》程乙本与庚辰本的异文统计？"),
        ("u10", "冷聚变实验的重复性争议现状？"),
    ]
    for uid, q in unanswerable[:count_unanswerable]:
        items.append(
            {
                "id": uid,
                "kind": "unanswerable",
                "question": q,
                "gold_note_id": None,
                "gold_chunk_id": None,
                "should_refuse": True,
            }
        )

    # 多跳：两处 gold **必须来自不同笔记**（round7：原来 10/10 同篇）
    by_note: Dict[str, List[str]] = {}
    for chunk_id, note_id, *_rest in picked:
        by_note.setdefault(note_id, []).append(chunk_id)
    note_ids = [n for n, ids in by_note.items() if ids]
    for i in range(count_multi):
        if len(note_ids) < 2:
            break
        a, b = note_ids[(i * 2) % len(note_ids)], note_ids[(i * 2 + 1) % len(note_ids)]
        items.append(
            {
                "id": f"m{i + 1:02d}",
                "kind": "multi_hop",
                "question": _MULTI_TEMPLATES[i % len(_MULTI_TEMPLATES)],
                "gold_note_id": None,
                "gold_chunk_ids": [by_note[a][0], by_note[b][0]],
                "gold_note_ids": [a, b],
                "should_refuse": False,
            }
        )

    with _goldens_file().open("w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    kinds = Counter(it["kind"] for it in items)
    # 多跳题的"两 gold 是否同篇"自检（round7 要求）
    same_note = sum(1 for it in items if it["kind"] == "multi_hop" and len(set(it.get("gold_note_ids") or [])) < 2)
    print(f"已生成 {len(items)} 题 → {_goldens_file()}")
    print("分布:", dict(kinds))
    print(f"多跳题中两 gold 同篇的: {same_note}（应为 0）")
    return 0


async def run(limit: Optional[int] = None, with_answers: bool = False, user_id: str | None = None) -> int:
    """跑评测：检索命中（Recall@K / MRR）+ 拒答是否正确。"""
    from sqlalchemy import select

    from app.config import get_settings
    from app.database import get_session_factory
    from app.models.user import User
    from app.services.rag_service import RAGService

    if not _goldens_file().exists():
        print("还没有评测集，先跑 --generate")
        return 1

    items = [json.loads(line) for line in _goldens_file().read_text(encoding="utf-8").splitlines() if line.strip()]
    if limit:
        items = items[:limit]  # round7：原来只会跑多跳题

    factory = get_session_factory()
    async with factory() as db:
        if user_id:
            uid = user_id
        else:
            first = (await db.execute(select(User).limit(1))).scalars().first()
            uid = first.id
            print(f"⚠️ 未指定 --user-id，使用用户表第一行：{uid}")

    # K 取配置里"进上下文的条数上限"（原来硬编码 5，实际返回 8 —— 指标名与口径不符）
    k = int(getattr(get_settings(), "rag_context_top_k", 8))

    rag = RAGService()
    rows: List[Dict[str, Any]] = []
    for it in items:
        r = await rag.retrieve_context(question=it["question"], user_id=uid)
        sources = (r.get("sources") or [])[:k]
        got_chunks = {s.get("chunk_id") for s in sources}
        gold = it.get("gold_chunk_id")
        golds = [g for g in (it.get("gold_chunk_ids") or ([gold] if gold else [])) if g]
        hit = bool(golds) and any(g in got_chunks for g in golds)
        rank = 0
        for idx, s in enumerate(sources, 1):
            if s.get("chunk_id") in golds:
                rank = idx
                break
        rows.append(
            {
                "id": it["id"],
                "kind": it["kind"],
                "hit": hit,
                "rank": rank,
                "refused": len(sources) == 0,
                "should_refuse": bool(it.get("should_refuse")),
                "status": r.get("retrieval_status"),
                "n_sources": len(sources),
            }
        )

    def pct(x: float) -> str:
        return f"{x * 100:.1f}%"

    answerable = [r for r in rows if r["kind"] != "unanswerable"]
    unanswerable = [r for r in rows if r["kind"] == "unanswerable"]
    hits = [r for r in answerable if r["hit"]]
    recall = len(hits) / len(answerable) if answerable else 0.0
    mrr = sum(1 / r["rank"] for r in hits if r["rank"]) / len(answerable) if answerable else 0.0
    refused_ok = [r for r in unanswerable if r["refused"]]
    refuse_acc = len(refused_ok) / len(unanswerable) if unanswerable else 0.0
    false_refuse = [r for r in answerable if r["refused"]]

    degraded = [r for r in rows if r["status"] == "vector_dim_mismatch"]
    bm25_only = [r for r in rows if r["status"] == "bm25_only"]

    report = [f"# QA 评测报告（{date.today().isoformat()}）", ""]
    if degraded or bm25_only:
        report += [
            "> ⚠️ **环境警告：本报告的数字不能当基线**",
            ">",
            f"> - 维度不符（`vector_dim_mismatch`）：{len(degraded)} 题 —— 向量检索整批跳过",
            f"> - 仅关键词（`bm25_only`）：{len(bm25_only)} 题 —— 问题编码失败",
            ">",
            "> 常见原因：空闲内存 < 4 GB 时嵌入 worker 降级到 `bge-small-zh-v1.5`（512 维），",
            "> 而库里是 `bge-m3`（1024 维）⇒ 全部跳过。**行为是对的，环境不对。**",
            "> 请在内存充足时重跑，再拿数字做对比。",
            "",
        ]
    report += [
        f"- 题量：{len(rows)}（可答 {len(answerable)}、不可答 {len(unanswerable)}）",
        f"- K = {k}（取 `rag_context_top_k`，不是硬编码 5）",
        f"- 检索状态分布：{_dist(rows, 'status')}",
        "",
        "## 表 1 · 检索命中",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| Recall@{k} | **{pct(recall)}** |",
        f"| MRR | {mrr:.3f} |",
        f"| 命中题数 | {len(hits)}/{len(answerable)} |",
        "",
        "## 表 2 · 拒答",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| 不可答题正确拒答率 | **{pct(refuse_acc)}** |",
        f"| 可答题被误拒 | {len(false_refuse)} 题 |",
        "",
        "## 表 3 · 引用覆盖",
        "",
        "未跑（需要 LLM 判分）。骨架阶段只跑检索层与拒答层 —— 零成本、可重复；",
        "`--with-answers` 尚未实装，见文件头的说明。",
        "",
        "## 明细",
        "",
        "| id | kind | hit | rank | refused | status |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        report.append(
            f"| {r['id']} | {r['kind']} | {'✅' if r['hit'] else '—'} | {r['rank'] or '—'} | "
            f"{'是' if r['refused'] else '否'} | {r['status']} |"
        )

    GOLDENS.mkdir(parents=True, exist_ok=True)
    _report_file().write_text("\n".join(report) + "\n", encoding="utf-8")

    print(f"Recall@{k} = {pct(recall)} | MRR = {mrr:.3f} | 拒答正确率 = {pct(refuse_acc)}")
    print(f"可答题误拒 {len(false_refuse)} 题")
    print(f"报告 → {_report_file()}")
    return 0


def _dist(rows: List[Dict[str, Any]], key: str) -> str:
    d: Counter[str] = Counter(str(r.get(key)) for r in rows)
    return "、".join(f"{k}×{v}" for k, v in sorted(d.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description="QA 评测骨架（A9）")
    parser.add_argument("--generate", action="store_true", help="从库里生成 60 题骨架")
    parser.add_argument("--run", action="store_true", help="跑评测并写报告")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 题")
    parser.add_argument("--with-answers", action="store_true", help="（未实装）LLM 判分")
    parser.add_argument("--user-id", default=None, help="用哪个用户的数据评测（默认取第一行并警告）")
    args = parser.parse_args()

    if args.generate:
        return asyncio.run(generate())
    if args.run:
        return asyncio.run(run(limit=args.limit, with_answers=args.with_answers, user_id=args.user_id))
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
