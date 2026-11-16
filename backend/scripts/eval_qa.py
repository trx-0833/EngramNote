"""QA 评测骨架（A9，`research/topics/qa-gap-triage.md`）。

报告要求：从现有笔记出 60 题（40 可答 / 10 不可答 / 10 多跳），每题标 gold chunk_id；
脚本跑三件事：**检索命中**、**引用覆盖**、**拒答是否正确**。

## 为什么"骨架"阶段不调 LLM

`faithfulness` 的正规做法要 LLM 判分（每轮一次付费调用）。骨架先跑**检索层与拒答层**
——这两层零成本、可重复、且正是 A8 要验收的东西（Recall@5）。加 `--with-answers`
才走 LLM 那条路（默认关）。

## 产物去哪

- 评测集与报告：`research/experiments/<date>-qa-eval/`（`research/` 整目录 gitignore，
  符合 AGENTS §7「调研产物不入库」）；
- 结论：由人写进 `docs/`。

## 用法

```bash
python scripts/eval_qa.py --generate          # 从库里抽 60 题骨架
python scripts/eval_qa.py --run               # 跑检索层 + 拒答层，打印三张表
python scripts/eval_qa.py --run --limit 20    # 只跑追问子集（A8 用）
```
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

GOLDENS = Path(__file__).resolve().parents[1] / ".." / "research" / "experiments" / "20261116-qa-eval"
GOLDENS = GOLDENS.resolve()


def _goldens_file() -> Path:
    return GOLDENS / "goldens.jsonl"


def _report_file() -> Path:
    return GOLDENS / "report.md"


async def generate(count_answerable: int = 40, count_unanswerable: int = 10, count_multi: int = 10) -> int:
    """从库里抽题：可答题用 chunk 的标题/首句做问题，金标是该 chunk。"""
    from sqlalchemy import select

    from app.database import get_session_factory
    from app.models.chunk import Chunk
    from app.models.note import Note

    GOLDENS.mkdir(parents=True, exist_ok=True)

    factory = get_session_factory()
    async with factory() as db:
        rows = (
            await db.execute(
                select(Chunk.id, Chunk.note_id, Chunk.content, Chunk.heading_path, Note.title)
                .join(Note, Note.id == Chunk.note_id)
                .where(
                    Chunk.has_embedding.is_(True),
                    Note.trashed_at.is_(None),
                    Chunk.char_count >= 120,
                )
                .limit(2000)
            )
        ).all()

    if not rows:
        print("库里没有可用的 chunk —— 先跑 embed_chunks.py")
        return 1

    rng = random.Random(20261116)  # 固定种子：可复现
    picked = rng.sample(rows, min(len(rows), count_answerable * 3))

    items: List[Dict[str, Any]] = []
    seen_notes: set[str] = set()
    for chunk_id, note_id, content, heading, note_title in picked:
        if len(items) >= count_answerable:
            break
        # 同一篇笔记最多出 2 题，避免题目全挤在一篇里
        if list(seen_notes).count(note_id) >= 2:
            continue
        seen_notes.add(note_id)
        # 问题形态：优先用标题，退化到首句的关键片段
        topic = (heading or "").split(">")[-1].strip() or (note_title or "")
        body = " ".join((content or "").split())
        snippet = body[:40]
        items.append(
            {
                "id": f"a{len(items) + 1:02d}",
                "kind": "answerable",
                "question": f"{topic}：{snippet}——这段具体要求是什么？",
                "gold_note_id": note_id,
                "gold_chunk_id": chunk_id,
                "should_refuse": False,
            }
        )

    # 不可答题：与语料完全无关的领域
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

    # 多跳题：需要两篇笔记一起答。金标取两篇不同笔记的 chunk（如果没有就退化成一跳）。
    note_ids = sorted(seen_notes)[: count_multi * 2]
    for i in range(count_multi):
        if len(note_ids) >= 2:
            a, b = note_ids[i % len(note_ids)], note_ids[(i + 1) % len(note_ids)]
            gold_ids = [r[0] for r in rows if r[1] in (a, b)][:2]
        else:
            gold_ids = []
        items.append(
            {
                "id": f"m{i + 1:02d}",
                "kind": "multi_hop",
                "question": "这两个系统的运行要求有什么共同点？",
                "gold_note_id": None,
                "gold_chunk_ids": gold_ids,
                "should_refuse": False,
            }
        )

    with _goldens_file().open("w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    kinds: Dict[str, int] = {}
    for it in items:
        kinds[it["kind"]] = kinds.get(it["kind"], 0) + 1
    print(f"已生成 {len(items)} 题 → {_goldens_file()}")
    print("分布:", kinds)
    return 0


async def run(limit: Optional[int] = None, with_answers: bool = False) -> int:
    """跑评测：检索命中（Recall@k / MRR）+ 拒答是否正确 +（可选）引用覆盖。"""
    from app.database import get_session_factory
    from app.models.user import User
    from app.services.rag_service import RAGService

    if not _goldens_file().exists():
        print("还没有评测集，先跑 --generate")
        return 1

    items = [json.loads(line) for line in _goldens_file().read_text(encoding="utf-8").splitlines() if line.strip()]
    if limit:
        items = [it for it in items if it["kind"] == "multi_hop"][:limit] or items[:limit]

    factory = get_session_factory()
    async with factory() as db:
        uid = (await db.execute(__import__("sqlalchemy").select(User).limit(1))).scalars().first().id

    rag = RAGService()
    rows: List[Dict[str, Any]] = []
    for it in items:
        r = await rag.retrieve_context(question=it["question"], user_id=uid)
        sources = r.get("sources") or []
        got_chunks = {s.get("chunk_id") for s in sources}
        gold = it.get("gold_chunk_id")
        golds = it.get("gold_chunk_ids") or ([gold] if gold else [])
        hit = bool(golds) and any(g in got_chunks for g in golds)
        rank = 0
        for idx, s in enumerate(sources, 1):
            if s.get("chunk_id") in golds:
                rank = idx
                break
        refused = len(sources) == 0
        rows.append(
            {
                "id": it["id"],
                "kind": it["kind"],
                "hit": hit,
                "rank": rank,
                "refused": refused,
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
    # 可答题被误拒（检索到空 sources）也是问题
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
            "> 常见原因：空闲内存 < 4 GB 时嵌入 worker 会降级到 `bge-small-zh-v1.5`（512 维），",
            "> 而库里是 `bge-m3`（1024 维）⇒ 全部跳过。**行为是对的，环境不对。**",
            "> 请在内存充足时重跑，再拿数字做对比。",
            "",
        ]
    report += [
        f"- 题量：{len(rows)}（可答 {len(answerable)}、不可答 {len(unanswerable)}）",
        f"- 检索状态分布：{_dist(rows, 'status')}",
        "",
        "## 表 1 · 检索命中",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| Recall@5 | **{pct(recall)}** |",
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
    ]
    if with_answers:
        report.append("（本轮未实装 --with-answers 的 LLM 判分，见脚本文件头。）")
    else:
        report.append("未跑（需要 LLM 判分，`--with-answers`；骨架阶段默认关）。")
    report += [
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

    print(f"Recall@5 = {pct(recall)} | MRR = {mrr:.3f} | 拒答正确率 = {pct(refuse_acc)}")
    print(f"可答题误拒 {len(false_refuse)} 题")
    print(f"报告 → {_report_file()}")
    return 0


def _dist(rows: List[Dict[str, Any]], key: str) -> str:
    d: Dict[str, int] = {}
    for r in rows:
        k = str(r.get(key))
        d[k] = d.get(k, 0) + 1
    return "、".join(f"{k}×{v}" for k, v in sorted(d.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description="QA 评测骨架（A9）")
    parser.add_argument("--generate", action="store_true", help="从库里生成 60 题骨架")
    parser.add_argument("--run", action="store_true", help="跑评测并写报告")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 题（或只跑多跳题）")
    parser.add_argument("--with-answers", action="store_true", help="额外跑 LLM 判分（付费）")
    args = parser.parse_args()

    if args.generate:
        return asyncio.run(generate())
    if args.run:
        return asyncio.run(run(limit=args.limit, with_answers=args.with_answers))
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
