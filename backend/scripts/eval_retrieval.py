#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
检索质量离线评测（overhaul-plan 阶段 2.9）
==========================================

回答一个此前**无法回答**的问题：**现在的检索到底有多准？**

## 为什么必须有它

阶段 2 的核心动作之一是 **2.3「索引源从卡片换成清洗后的原文」**。
这个改动如果只凭"原文当然比摘要好"的直觉就去做，是无法判断成败的：
改完后检索可能变好、变差、或对一半问题变好对另一半变差，而**没有任何
基线可以对比**。本文件提供那条基线。

同一条理由也适用于 RRF 权重（2.6 未做的部分）、chunk_size 调参、
以及 2.1 统一分块 —— 凡是"改了检索行为"的改动，都必须先有基线。

## 真值从哪来（关键设计）

**不能用"卡片出题 → 期望命中该卡片"**：那会让卡片语料拿到满分，
无法公平比较语料切换。本项目真库里有更好的东西：

    quiz_items.question  是 LLM 基于某张卡片出的题
    quiz_items.card_id   → knowledge_cards
    knowledge_cards.source_text  = 该知识点的**原文出处**

`source_text` 是**原文段落**，不是卡片摘要。于是：

- 判相关用的是"检索到的内容与原文段落是否重叠"，
  这个标准对**卡片语料**与**原文 chunk 语料**同样适用 → 可公平比较
- 真值来自真实数据，不是造出来的；实测真库 1058 条 quiz **全部**可用
  （1058/1058 有 `card_id`，且对应卡片 `source_text` 非空）

## 指标

- **Recall@5**：top-5 里至少有一条与真值重叠的问题占比
- **MRR**：第一条命中所在的排名倒数（未命中记 0），反映"命中得够不够靠前"
- **无候选率**：BM25 一条都没返回（分数全为 0）的问题占比 ——
  这类问题在线上会直接走"资料中没有找到"分支

## 诚实的边界（必须写清楚）

1. 评测的 **BM25 通道**（词法检索）。向量通道需要加载 BGE-M3 并跑 Celery，
   本脚本不覆盖；线上是 BM25 + 向量 RRF 融合，因此这里的数字是
   **下界性质的通道基线**，不是端到端质量。
2. 问题集是 **LLM 基于资料生成的**，措辞天然更接近原文用词，
   因此对词法检索**偏乐观**。真实用户提问会更口语化、更间接。
   这个偏差是已知且固定的，用于**相对比较**（改前 vs 改后）仍然有效。
3. 真值判据是字符串重叠（见 `_overlaps`），会漏掉"语义等价但用词不同"的
   命中 → 又一处偏保守。

结论：**这些数字不适合当作产品对外指标，适合当作改动前后的对比基线。**

用法：
    python scripts/eval_retrieval.py                     # 两套语料都测
    python scripts/eval_retrieval.py --limit 200         # 快速抽样
    python scripts/eval_retrieval.py --corpus chunks     # 只测原文 chunk 语料
    python scripts/eval_retrieval.py --json out.json     # 落盘结果
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

#: 判重叠时使用的指纹长度（字符）。太短会误判（"水电站"到处都是），
#: 太长会漏判（chunk 边界恰好切在中间）。30 是在本项目资料上的折中。
FINGERPRINT_LEN = 30

DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "db", "engramnote.db"
)
DEFAULT_CHROMA = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "chroma"
)


# ---------------------------------------------------------------------------
# 真值
# ---------------------------------------------------------------------------

def load_eval_set(db_path: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """从真实库读出 (问题, 原文真值) 评测集

    Returns:
        [{question, expected_text, card_id, note_id, card_title, card_content}, ...]
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        sql = """
            SELECT qi.question, kc.source_text, qi.card_id, qi.note_id,
                   kc.title, kc.content
              FROM quiz_items qi
              JOIN knowledge_cards kc ON kc.id = qi.card_id
             WHERE qi.question IS NOT NULL AND TRIM(qi.question) <> ''
               AND kc.source_text IS NOT NULL AND TRIM(kc.source_text) <> ''
             ORDER BY qi.id
        """
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = con.execute(sql).fetchall()
    finally:
        con.close()

    return [
        {
            "question": q,
            "expected_text": st,
            "card_id": cid,
            "note_id": nid,
            "card_title": title,
            "card_content": content,
        }
        for q, st, cid, nid, title, content in rows
    ]


def _normalize(text: str) -> str:
    """压缩空白：Markdown 的换行/缩进不应影响"内容是否一致"的判断

    实测未命中样例里有这种情况：真值是编号列表
    `3.1.1 线路断路器…\\n3.1.2 …`，检索命中的是同一份内容的另一种换行。
    精确子串判据会把它判为未命中 —— 那是**格式差异**，不是检索错误。
    """
    return " ".join((text or "").split())


def _char_ngrams(text: str, n: int = 3) -> set:
    t = _normalize(text)
    if len(t) <= n:
        return {t} if t else set()
    return {t[i:i + n] for i in range(len(t) - n + 1)}


#: 短串回退分支的最小长度。低于此长度的检索内容**不参与**回退判定。
MIN_SHORT_SIDE = 8


def _overlaps(expected: str, actual: str, n: int = FINGERPRINT_LEN) -> bool:
    """**严格**判据：真值或检索内容的"前 n 字"必须出现在对方之中

    双向判据（任一成立即算命中）：
      1. 真值的前 n 字出现在检索内容中 —— 覆盖"检索内容比真值大"
      2. 检索内容的前 n 字出现在真值中 —— 覆盖"检索内容比真值小"
         （原文 chunk 被切开时，单个 chunk 可能只是真值的一段）

    取"前 n 字"而不是任意 n 字子串：任意子串会让常见功能词
    （"的"、"系统"）造成大量误命中，指标会虚高到没有意义。

    先做空白归一化再比对（见 `_normalize`）。

    **这是偏保守的判据**：措辞略有不同（"我国" vs "全国"）但内容一致的
    正确命中会被判为未命中。因此本脚本同时报告宽松判据（`_overlaps_loose`），
    两档一起看才能分辨"检索真的错了"与"判据太严"。

    ## 短串回退的边界（`MIN_SHORT_SIDE`）

    两侧都短于 n 时回退为"互相包含"判断。但一个过短的检索内容会因此
    命中任何包含它的长真值 —— 例如 `"拉哇水电站"`（5 字）会命中
    `"…拉哇水电站装设多台水轮发电机组…"`。这类"只取回了一个实体名"
    的结果不该算作找到了答案。因此回退分支要求**两侧都 ≥ MIN_SHORT_SIDE**；
    更短的检索内容必须与真值逐字相等才算命中。
    这个边界由 `test_partial_overlap_below_threshold` 守着。
    """
    e = _normalize(expected)
    a = _normalize(actual)
    if not e or not a:
        return False
    if len(e) >= n and e[:n] in a:
        return True
    if len(a) >= n and a[:n] in e:
        return True
    # 两边都短于 n：仅当两侧都足够长时才允许互相包含
    if len(e) < n or len(a) < n:
        if len(e) >= MIN_SHORT_SIDE and len(a) >= MIN_SHORT_SIDE:
            return e in a or a in e
        return e == a
    return False


#: 宽松判据的包含度阈值
CONTAINMENT_THRESHOLD = 0.5


def _containment(expected: str, actual: str) -> float:
    """真值被检索内容覆盖的比例（长度中立）

    = |真值的字符 3-gram ∩ 检索内容的字符 3-gram| / |真值的字符 3-gram|

    ## 为什么不是 Jaccard（第一版用错了，实测数据如下）

    第一版宽松判据用字符 3-gram 的 **Jaccard**，结果出现了一边倒：
    卡片语料 64% 命中、原文 chunk 语料只有 9%。看似"卡片远好于原文"，
    实际是**判据本身对长度不对称有系统性偏差**：

        真值 200 字，检索内容 2000 字（原文 chunk 的常态）
        → 交集 ≈ 200 字，并集 ≈ 2000 字 → Jaccard ≈ 0.10（判为未命中）
        真值 200 字，检索内容 186 字（卡片的常态：中位长度比 0.93）
        → 交集 ≈ 180 字，并集 ≈ 206 字 → Jaccard ≈ 0.87（判为命中）

    同一个"正确命中"，只因为检索单元比真值大就得到低分 —— 这是
    **分母被并集主导**造成的，与被检索内容是否相关无关。

    包含度把分母固定为**真值**，于是"检索单元更大"不再受罚，
    而"检索单元更小"（chunk 边界把真值切开）也只按实际覆盖比例计分。

    ## 判据可信度的实测证据（本项目真库，各 120~200 条）

    | 对象 | 包含度中位数 | ≥0.5 占比 |
    |---|---|---|
    | 来源卡片 vs 自己的真值 | 0.773 | 83.0% |
    | 语料中最佳 chunk vs 真值 | **1.000** | **94.2%** |
    | **随机无关 chunk（对照组）** | **0.000** | **0.0%** |

    对照组 0% 说明它不会给无关内容送分；最佳 chunk 中位数 1.0 说明
    原文 chunk 通常**完整包含**真值。一个会给对照组送分、或对正确命中
    给低分的判据都不能用 —— 上面那张表就是为了排除这两种情况。
    """
    e = _char_ngrams(expected)
    if not e:
        return 0.0
    a = _char_ngrams(actual)
    if not a:
        return 0.0
    return len(e & a) / len(e)


def _overlaps_loose(expected: str, actual: str) -> bool:
    """**宽松**判据：真值被覆盖 ≥ `CONTAINMENT_THRESHOLD`

    用于区分两种"未命中"：
      - 检索确实取回了不相关内容 → 两档判据都不命中
      - 检索取回了包含该原文的内容但措辞/格式有差异 → 严格不命中、宽松命中
    """
    return _containment(expected, actual) >= CONTAINMENT_THRESHOLD


# ---------------------------------------------------------------------------
# 语料
# ---------------------------------------------------------------------------

def load_card_corpus(db_path: str) -> List[Dict[str, Any]]:
    """卡片语料（= 线上 `_get_user_cards` 的等价查询，只取 5 列）"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute("""
            SELECT kc.id, kc.note_id, kc.title, kc.content, kc.chapter_title
              FROM knowledge_cards kc
             WHERE kc.note_id IS NULL
                OR NOT EXISTS (
                     SELECT 1 FROM notes n
                      WHERE n.id = kc.note_id AND n.trashed_at IS NOT NULL
                   )
        """).fetchall()
    finally:
        con.close()
    return [
        {"card_id": cid, "note_id": nid, "title": t or "",
         "content": c or "", "chapter_title": ch}
        for cid, nid, t, c, ch in rows
    ]


def load_chunk_corpus(chroma_dir: str) -> List[Dict[str, Any]]:
    """原文 chunk 语料（Chroma 里实际索引的内容）

    这正是 **2.3 想要的语料** —— 它不是卡片摘要，而是清洗后原文的分块。
    所以本评测能直接给出"卡片 vs 原文"的对比，不必等到改动之后。

    注意：Chroma 的 chunk 没有 `note_id`（元数据里只有行号），
    所以该语料的 `note_id` 为 None；这不影响本评测 ——
    判相关用的是内容重叠，不是 id 匹配。
    """
    try:
        import chromadb
    except ImportError:
        return []

    if not os.path.isdir(chroma_dir):
        return []

    client = chromadb.PersistentClient(path=chroma_dir)
    corpus: List[Dict[str, Any]] = []
    for col in client.list_collections():
        if col.count() == 0:
            continue
        got = col.get(include=["documents", "metadatas"])
        for doc, meta in zip(got["documents"], got["metadatas"], strict=False):
            if not doc:
                continue
            corpus.append({
                "card_id": None,
                "note_id": None,
                "title": col.name,
                "content": doc,
                "chapter_title": (meta or {}).get("heading_context"),
            })
    return corpus


# ---------------------------------------------------------------------------
# 指标
# ---------------------------------------------------------------------------

def evaluate(
    eval_set: List[Dict[str, Any]],
    corpus: List[Dict[str, Any]],
    index,
    top_k: int = 5,
) -> Dict[str, Any]:
    """在给定语料上跑完整评测集

    同时计算**严格**与**宽松**两档判据（见 `_overlaps` / `_overlaps_loose`）。
    两档差距大 = 检索其实取回了正确内容，只是措辞/格式有差异；
    两档都低 = 检索真的没找到。只看一档会得出错误结论。
    """
    strict_hits = 0
    loose_hits = 0
    strict_rr = 0.0
    loose_rr = 0.0
    empty = 0
    missed: List[Dict[str, Any]] = []
    loose_only = 0  # 宽松命中但严格未命中：判据差异导致的"假未命中"

    for case in eval_set:
        results = index.search(case["question"], top_k=top_k)
        if not results:
            empty += 1

        s_rank = 0
        l_rank = 0
        for i, item in enumerate(results, start=1):
            content = item.get("content") or ""
            if not l_rank and _overlaps_loose(case["expected_text"], content):
                l_rank = i
            if not s_rank and _overlaps(case["expected_text"], content):
                s_rank = i
            if s_rank and l_rank:
                break

        if s_rank:
            strict_hits += 1
            strict_rr += 1.0 / s_rank
        if l_rank:
            loose_hits += 1
            loose_rr += 1.0 / l_rank
        if l_rank and not s_rank:
            loose_only += 1

        if not s_rank:
            missed.append({
                "question": case["question"],
                "expected_head": case["expected_text"].strip()[:60],
                "loose_hit_rank": l_rank or None,
                "got": [((r.get("content") or "").strip()[:40]) for r in results[:3]],
            })

    total = len(eval_set)
    return {
        "corpus_size": len(corpus),
        "queries": total,
        "strict": {
            "recall_at_k": round(strict_hits / total, 4) if total else 0.0,
            "mrr": round(strict_rr / total, 4) if total else 0.0,
        },
        "loose": {
            "recall_at_k": round(loose_hits / total, 4) if total else 0.0,
            "mrr": round(loose_rr / total, 4) if total else 0.0,
        },
        #: 宽松命中而严格未命中的条数：判据差异，不是检索错误
        "strict_miss_but_loose_hit": loose_only,
        "empty_result_rate": round(empty / total, 4) if total else 0.0,
        "missed": missed,
    }


def _print_report(name: str, report: Dict[str, Any], top_k: int, show_missed: int) -> None:
    print(f"\n{'=' * 70}")
    print(f"语料: {name}")
    print(f"{'=' * 70}")
    print(f"  语料规模      : {report['corpus_size']}")
    print(f"  评测问题数    : {report['queries']}")
    print(f"  Recall@{top_k} 严格 : {report['strict']['recall_at_k']:.2%}")
    print(f"  Recall@{top_k} 宽松 : {report['loose']['recall_at_k']:.2%}")
    print(f"  MRR 严格/宽松 : {report['strict']['mrr']:.4f} / {report['loose']['mrr']:.4f}")
    print(f"  无候选比例    : {report['empty_result_rate']:.2%}")
    print(f"  严格未命中但宽松命中: {report['strict_miss_but_loose_hit']}"
          f"（措辞/格式差异，非检索错误）")
    missed = [m for m in (report.get("missed") or []) if not m.get("loose_hit_rank")]
    print(f"  两档都未命中  : {len(missed)}（真正的检索失败）")
    if show_missed and missed:
        print(f"\n  真正未命中样例（前 {min(show_missed, len(missed))} 条）:")
        for m in missed[:show_missed]:
            print(f"    Q: {m['question'][:70]}")
            print(f"      期望: {m['expected_head']}")
            for g in m["got"]:
                print(f"      实际: {g}")
            print()


def main() -> int:
    ap = argparse.ArgumentParser(description="检索质量离线评测（阶段 2.9）")
    ap.add_argument("--db", default=DEFAULT_DB, help="数据库路径（只读打开）")
    ap.add_argument("--chroma", default=DEFAULT_CHROMA, help="Chroma 持久化目录")
    ap.add_argument("--limit", type=int, default=0, help="只评测前 N 条问题（0=全部）")
    ap.add_argument("--top-k", type=int, default=5, help="Recall@k 的 k")
    ap.add_argument("--corpus", choices=["cards", "chunks", "both"], default="both")
    ap.add_argument("--json", default="", help="把结果写入该 JSON 文件")
    ap.add_argument("--show-missed", type=int, default=3, help="打印多少条未命中样例")
    args = ap.parse_args()

    from app.services.rag_service import RAGService

    eval_set = load_eval_set(args.db, args.limit or None)
    if not eval_set:
        print("评测集为空：真库中没有可用的 (quiz.question, card.source_text) 组合。")
        return 2

    print("=" * 70)
    print("检索质量离线评测（阶段 2.9）")
    print("=" * 70)
    print(f"  数据库        : {os.path.abspath(args.db)}")
    print(f"  评测集        : {len(eval_set)} 条 (问题, 原文真值)")
    print()
    print("  说明：本评测只覆盖 BM25（词法）通道；向量通道需加载 BGE-M3 + Celery，")
    print("        未纳入。问题集由 LLM 依据资料生成，对词法检索偏乐观 ——")
    print("        这些数字用于**改动前后对比**，不是对外质量指标。")

    out: Dict[str, Any] = {"eval_set_size": len(eval_set), "reports": {}}

    if args.corpus in ("cards", "both"):
        cards = load_card_corpus(args.db)
        index = RAGService.build_bm25_index(cards)
        report = evaluate(eval_set, cards, index, top_k=args.top_k)
        _print_report("卡片语料（当前线上）", report, args.top_k, args.show_missed)
        out["reports"]["cards"] = {k: v for k, v in report.items() if k != "missed"}

    if args.corpus in ("chunks", "both"):
        chunks = load_chunk_corpus(args.chroma)
        if not chunks:
            print("\n[跳过] 原文 chunk 语料为空（Chroma 目录不存在或无数据）")
        else:
            index = RAGService.build_bm25_index(chunks)
            report = evaluate(eval_set, chunks, index, top_k=args.top_k)
            _print_report("原文 chunk 语料（2.3 的目标）", report, args.top_k, args.show_missed)
            out["reports"]["chunks"] = {k: v for k, v in report.items() if k != "missed"}

    if "cards" in out["reports"] and "chunks" in out["reports"]:
        c = out["reports"]["cards"]
        k = out["reports"]["chunks"]
        print(f"\n{'=' * 70}")
        print("对比（原文 chunk 相对卡片语料）")
        print("=" * 70)
        for label in ("strict", "loose"):
            print(f"  [{label}] Recall@{args.top_k}: "
                  f"{c[label]['recall_at_k']:.2%} → {k[label]['recall_at_k']:.2%} "
                  f"({k[label]['recall_at_k'] - c[label]['recall_at_k']:+.2%})   "
                  f"MRR: {c[label]['mrr']:.4f} → {k[label]['mrr']:.4f} "
                  f"({k[label]['mrr'] - c[label]['mrr']:+.4f})")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"\n结果已写入: {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
