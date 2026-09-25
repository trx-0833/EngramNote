#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""关系判定质量离线测量：现在的「建边」方法到底建出了什么

================================================================================
为什么需要这个脚本
================================================================================

真实库里 1181 张卡片只有 120 张有边（260 条），**1061 张是孤点**。而判定方法
（`graph_service.auto_suggest_relations`）本身在真实语料上的表现从未被量过：
它取了哪些卡、用了什么文本、卡在哪个阈值上，全都没有数字。

本脚本**只测量、不修改**，回答三个问题：

  1. 候选对的相似度到底分布在哪？（"阈值 0.75 是不是太高"的直接答案）
  2. 各阈值下能建多少条边、覆盖多少张卡？
  3. **这些边建得对不对**？（抽样判准 —— 前两个问题只说明"能建多少"，
     不说明"建得对不对"；没有第 3 问，调阈值就是拿"边变多了"冒充"变好了"）

================================================================================
它**不**做什么
================================================================================

不写库（`sqlite3` 以 `mode=ro` 只读打开）、不建关系、不调 LLM、不联网、
不改任何文件（除 `--out` / `--emit-labels` 指定的输出）。

⚠️ 判准由**人**做（或把 `--out` 的表格交给别的工具判），脚本不代劳 ——
因为 `LLMService.chat` 会写 `llm_call` 记账行（`llm/gateway.py:416` 的
`record_call`），一旦在这里调 LLM，这个脚本就不再是只读的了。

================================================================================
三种"边数"必须分开（本文件的核心判据，别混成一个）
================================================================================

  1. **可建边**（eligible）：当前规则允许、相似度达标、库里还没有的卡片对。
     这是"方法的能力上限"。
  2. **实际新建**：调用一次会真正写入的数量 —— 它还要减去库里已有的关系。
  3. **覆盖**：这些边碰到了多少张卡。**孤点问题看的是这个，不是边数**：
     100 条边全挤在 10 张卡上，孤点一个都没少。

报告里三个数并列，不合并。

================================================================================
用法
================================================================================

    # 全流程（加载本地 BGE-M3 编码，约 2 分钟）
    python scripts/measure_relation_quality.py

    # 复用已算好的向量（改抽样参数时用，秒级）
    python scripts/measure_relation_quality.py --vectors /tmp/card_vecs.npy

    # 把抽样表写成 markdown，供人工/外部工具判准
    python scripts/measure_relation_quality.py --out /tmp/sample.md --emit-labels /tmp/labels.json

    # 拿填好的标注算各档精确率
    python scripts/measure_relation_quality.py --vectors /tmp/card_vecs.npy --labels /tmp/labels.json

退出码：0 = 跑完了；2 = **检查自身空转**（库读不到 / 候选不足 / 标注没填完）。
"""

import argparse
import json
import os
import random
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "db", "engramnote.db"
)

#: 抽样随机种子。**固定**：同一份语料两次跑必须得到同一批样，
#: 否则"判准结果"不可复现，也就没法拿它当决策依据。
SAMPLE_SEED = 20260926

#: 每个抽样组取多少对（"现状规则"那组是全取，见 GROUPS）
SAMPLE_PER_GROUP = 12


# ============================================================================
# 抽样分组：每一组对应一个"如果这样改，会建出什么"的假设
# ============================================================================
#
# `rule` 三种：
#   current —— 现在的规则：跨笔记 + 阈值
#   chapter —— 阶段 1 的规则：同笔记**同章节**才跳过 + 阈值
#   topk    —— 阶段 1 + 阶段 2 的规则：同章节跳过 + 每卡 Top-K + 绝对下界
GROUPS: List[Tuple[str, str, Dict, int]] = [
    ("A", "现状规则：跨笔记 & ≥0.75", {"rule": "current", "threshold": 0.75}, 0),
    ("B", "章节粒度 & ≥0.55", {"rule": "chapter", "threshold": 0.55}, SAMPLE_PER_GROUP),
    ("C", "章节粒度 & ≥0.65", {"rule": "chapter", "threshold": 0.65}, SAMPLE_PER_GROUP),
    ("D", "章节粒度 & ≥0.75", {"rule": "chapter", "threshold": 0.75}, SAMPLE_PER_GROUP),
    (
        "E",
        "提议方法：章节粒度 + 每卡Top-3 + 下界0.55",
        {"rule": "topk", "k": 3, "floor": 0.55},
        SAMPLE_PER_GROUP,
    ),
    (
        "F",
        "对照：章节粒度 + 每卡Top-5 + 下界0.55",
        {"rule": "topk", "k": 5, "floor": 0.55},
        SAMPLE_PER_GROUP,
    ),
]


# ============================================================================
# 只读取数：**照搬** auto_suggest_relations 的取数与文本构造
# ============================================================================


def _preflight_db(db_path: str) -> Optional[str]:
    """先验库、再 import 应用层：库读不到是最常见的失败（新克隆 / CI 上没有真库）"""
    if not os.path.exists(db_path):
        return f"库不存在：{db_path}"
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        n = con.execute("SELECT count(*) FROM knowledge_cards").fetchone()[0]
        con.close()
    except sqlite3.Error as exc:
        return f"库打不开或表缺失：{exc}"
    if n == 0:
        return "knowledge_cards 是空表"
    return None


def _fail_db_unavailable(problem: str) -> int:
    print("!" * 72)
    print(f"[ERROR] {problem}")
    print("        --db 默认指向 backend/data/db/engramnote.db，而真实资料库")
    print("        **不入版本控制**（只存在于有资料的那台机器上），因此新克隆与")
    print("        CI 上必须显式指定：python scripts/measure_relation_quality.py --db <路径>")
    print("        退出码 2 = 检查自身空转（0 才代表「跑完了」）。")
    print("!" * 72)
    return 2


def load_candidates(
    db_path: str, limit: int, user_id: str = ""
) -> Tuple[List[dict], Set[Tuple[str, str]]]:
    """取候选卡片 —— 与 `graph_service.auto_suggest_relations` 的 SQL 同口径

    同口径这件事是硬要求：**测量对象就是那段代码**，取数一旦不一致，
    量到的就不是它在真实库上的行为。两处容易写歪的地方：

    * **回收站过滤**用 `LEFT JOIN` + `n.trashed_at IS NULL`，与 `Note.not_trashed`
      的语义一致（`models/note.py:155` 起：note 端为 NULL **或** 未进回收站时可见）。
      写成 `JOIN` 会把 `note_id` 为空的卡片悄悄丢掉 —— 那是另一批数据。
    * **用户**取库里卡片最多的那个（本项目是单用户自托管；多用户时用 `--user-id` 指定）。
    """
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
            SELECT c.id, c.title, c.content, c.note_id, c.chapter_title
            FROM knowledge_cards c
            LEFT JOIN notes n ON n.id = c.note_id
            WHERE c.user_id = ?
              AND (n.trashed_at IS NULL)
            ORDER BY c.updated_at DESC
            LIMIT ?
            """,
            (uid, limit),
        ).fetchall()
        existing = {
            tuple(sorted((a, b)))
            for a, b in con.execute(
                "SELECT card_id_1, card_id_2 FROM card_relations "
                "WHERE card_id_1 IS NOT NULL AND card_id_2 IS NOT NULL"
            )
        }
    finally:
        con.close()
    cards = [
        {
            "id": r[0],
            "title": r[1],
            "content": r[2],
            "note_id": r[3],
            "chapter": r[4] or "",
        }
        for r in rows
    ]
    return cards, existing


def build_texts(cards: Sequence[dict]) -> List[str]:
    """文本构造也**照搬** `graph_service._build_card_embedding_text`"""
    from app.services.graph_service import _build_card_embedding_text

    return [_build_card_embedding_text(c["title"], c["content"]) for c in cards]


def encode(texts: Sequence[str], vectors_path: str, save_path: str, model_dir: str) -> np.ndarray:
    if vectors_path and os.path.exists(vectors_path):
        vecs = np.load(vectors_path)
        if vecs.shape[0] == len(texts):
            print(f"复用向量：{vectors_path}（{vecs.shape}）")
            return vecs
        print(f"[WARN] 向量数与候选数不符（{vecs.shape[0]} != {len(texts)}），重新编码")

    if not os.path.isdir(model_dir):
        raise SystemExit(
            f"[ERROR] 本地模型目录不存在：{model_dir}\n"
            "        本脚本**不下载模型**（下载是副作用）。请用 --model-dir 指定，"
            "或先跑 python check_env.py --fix 把 BGE-M3 拉下来。"
        )

    print(f"加载嵌入模型（**独立进程**，不进主进程）：{model_dir}")
    import time

    t0 = time.perf_counter()
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_dir)
    print(f"  模型就绪 {time.perf_counter() - t0:.1f}s，维度 {model.get_sentence_embedding_dimension()}")
    t1 = time.perf_counter()
    vecs = model.encode(list(texts), normalize_embeddings=True, batch_size=32, show_progress_bar=False)
    print(f"  编码 {len(texts)} 条耗时 {time.perf_counter() - t1:.1f}s")
    if save_path:
        np.save(save_path, vecs)
        print(f"  向量已存：{save_path}")
    return vecs


# ============================================================================
# 规则：从相似度矩阵得到"可建边"的卡片对
# ============================================================================


def eligible_pairs(sim: np.ndarray, cards: Sequence[dict], spec: Dict) -> Set[Tuple[int, int]]:
    """按某条规则算出可建边的**位置对**集合（(i, j)，i < j）"""
    n = len(cards)
    out: Set[Tuple[int, int]] = set()
    rule = spec["rule"]

    def same_note(i: int, j: int) -> bool:
        return cards[i]["note_id"] == cards[j]["note_id"]

    def same_chapter(i: int, j: int) -> bool:
        # 同笔记 **且** 章节字符串相同。章节为空的两张同笔记卡也算同章节 ——
        # 保守跳过，退化成现状行为，不会比现在更糟。
        return same_note(i, j) and cards[i]["chapter"] == cards[j]["chapter"]

    if rule in ("current", "chapter"):
        th = spec["threshold"]
        for i in range(n):
            for j in range(i + 1, n):
                if sim[i][j] < th:
                    continue
                if same_chapter(i, j):
                    continue
                if rule == "current" and same_note(i, j):
                    continue
                out.add((i, j))
        return out

    if rule == "topk":
        k = spec["k"]
        floor = spec["floor"]
        for i in range(n):
            cand = [
                (sim[i][j], j)
                for j in range(n)
                if j != i and sim[i][j] >= floor and not same_chapter(i, j)
            ]
            cand.sort(reverse=True)
            for _s, j in cand[:k]:
                out.add((min(i, j), max(i, j)))
        return out

    raise ValueError(f"未知规则：{rule}")


def pct(a: np.ndarray, q: float) -> float:
    return float(np.percentile(a, q)) if len(a) else float("nan")


# ============================================================================
# 抽样与判准
# ============================================================================


def sample_group(
    eligible: Set[Tuple[int, int]],
    sim: np.ndarray,
    cards: Sequence[dict],
    size: int,
    rng: random.Random,
) -> List[Tuple[int, int]]:
    """从可建边集合里抽样。

    **按类别分层**：跨笔记 / 同笔记跨章节各取一半（类别不够就全取另一类）。
    不这么做的话，样本会被对数多的那一类淹没，"同章节规则会不会引入噪音"
    这个问题就永远得不到回答。
    """
    cross = [p for p in eligible if cards[p[0]]["note_id"] != cards[p[1]]["note_id"]]
    cross_chapter = [p for p in eligible if cards[p[0]]["note_id"] == cards[p[1]]["note_id"]]

    if size <= 0:
        picked = list(eligible)
    else:
        half = max(1, size // 2)
        picked = rng.sample(cross, min(half, len(cross)))
        picked += rng.sample(cross_chapter, min(size - len(picked), len(cross_chapter)))
        if len(picked) < size:
            rest = [p for p in eligible if p not in picked]
            picked += rng.sample(rest, min(size - len(picked), len(rest)))

    return sorted(picked, key=lambda p: -sim[p[0]][p[1]])


def render_sample_markdown(
    groups: Dict[str, List[Tuple[int, int]]],
    sim: np.ndarray,
    cards: Sequence[dict],
) -> str:
    lines = ["# 关系判定抽样（供判准）", ""]
    lines.append("判据：这两张卡片**是否确实相关**（含前置/后续/对比这类有向关系）。")
    lines.append("判「是」记 `true`，判「否」记 `false`，填进 `--emit-labels` 产出的 JSON。")
    lines.append("")
    for gid, spec_label, _spec, _n in GROUPS:
        pairs = groups.get(gid, [])
        lines.append(f"## {gid}. {spec_label}（{len(pairs)} 对）")
        lines.append("")
        if not pairs:
            lines.append("_该档没有可建边 —— 这本身就是一个结论。_")
            lines.append("")
            continue
        lines.append("| # | 相似度 | 类别 | 卡片 A | 卡片 B | 章节 A | 章节 B |")
        lines.append("|---|---|---|---|---|---|---|")
        for idx, (i, j) in enumerate(pairs):
            kind = "跨笔记" if cards[i]["note_id"] != cards[j]["note_id"] else "同笔记跨章节"
            a, b = cards[i], cards[j]
            lines.append(
                f"| {gid}:{idx} | {sim[i][j]:.3f} | {kind} | {a['title']} | {b['title']} "
                f"| {a['chapter'] or '—'} | {b['chapter'] or '—'} |"
            )
        lines.append("")
    return "\n".join(lines)


def precision_report(labels: Dict[str, bool], groups: Dict[str, List[Tuple[int, int]]]) -> int:
    print("\n" + "=" * 78)
    print("抽样精确率（判「确实相关」的比例）")
    print("=" * 78)
    unfilled = []
    for gid, spec_label, _spec, _n in GROUPS:
        pairs = groups.get(gid, [])
        if not pairs:
            print(f"  {gid}. {spec_label:38s} 无可建边")
            continue
        yes = no = 0
        for idx in range(len(pairs)):
            v = labels.get(f"{gid}:{idx}")
            if v is None:
                unfilled.append(f"{gid}:{idx}")
            elif v:
                yes += 1
            else:
                no += 1
        done = yes + no
        rate = f"{yes / done * 100:.0f}%" if done else "—"
        print(f"  {gid}. {spec_label:38s} 判准 {done}/{len(pairs)} 对 ｜ 相关 {yes} ｜ 精确率 {rate}")

    if unfilled:
        print(f"\n  [WARN] 还有 {len(unfilled)} 对没判：{', '.join(unfilled[:10])}"
              f"{' …' if len(unfilled) > 10 else ''}")
        return 2
    return 0


# ============================================================================


def main() -> int:
    ap = argparse.ArgumentParser(description="关系判定质量离线测量（只读）")
    ap.add_argument("--db", default=DEFAULT_DB, help="数据库路径（只读打开）")
    ap.add_argument("--user-id", default="", help="指定用户（默认取卡片最多的那个）")
    ap.add_argument("--limit", type=int, default=200,
                    help="候选卡片数上限（默认 200，与 MAX_CARDS_FOR_SUGGEST 同值）")
    ap.add_argument("--model-dir", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "data", "models", "Xorbits", "bge-m3"),
        help="本地嵌入模型目录（**不下载**）")
    ap.add_argument("--vectors", default="", help="复用已算好的向量 .npy")
    ap.add_argument("--save-vectors", default="", help="把向量写到该 .npy")
    ap.add_argument("--out", default="", help="把抽样表写成 markdown")
    ap.add_argument("--emit-labels", default="", help="写出待填的标注模板 JSON")
    ap.add_argument("--labels", default="", help="已填好的标注 JSON，用于算精确率")
    args = ap.parse_args()

    problem = _preflight_db(args.db)
    if problem:
        return _fail_db_unavailable(problem)

    cards, existing = load_candidates(args.db, args.limit, args.user_id)
    if len(cards) < 2:
        print(f"[ERROR] 候选不足 2 张（取到 {len(cards)} 张）—— 检查自身空转，退出码 2")
        return 2

    note_counts: Dict[str, int] = {}
    for c in cards:
        note_counts[c["note_id"]] = note_counts.get(c["note_id"], 0) + 1

    print("=" * 78)
    print("语料概况（取数与文本构造与 auto_suggest_relations 同口径）")
    print("=" * 78)
    print(f"  候选卡片        {len(cards)} 张（limit={args.limit}）")
    print(f"  落在            {len(note_counts)} 篇笔记：{sorted(note_counts.values(), reverse=True)}")
    print(f"  库内已有关系    {len(existing)} 对")
    print(f"  章节标题非空    {sum(1 for c in cards if c['chapter'])}/{len(cards)}")

    texts = build_texts(cards)
    print(f"  文本长度        min {min(map(len, texts))} / 中位 {int(np.median([len(t) for t in texts]))} / max {max(map(len, texts))}")

    vecs = encode(texts, args.vectors, args.save_vectors, args.model_dir)
    sim = np.asarray(vecs) @ np.asarray(vecs).T

    # ── 候选对拆分与相似度分位 ──
    cross, same_chap_x, same_chap = [], [], []
    for i in range(len(cards)):
        for j in range(i + 1, len(cards)):
            if cards[i]["note_id"] != cards[j]["note_id"]:
                cross.append(sim[i][j])
            elif cards[i]["chapter"] == cards[j]["chapter"]:
                same_chap.append(sim[i][j])
            else:
                same_chap_x.append(sim[i][j])
    cross, same_chap_x, same_chap = map(np.array, (cross, same_chap_x, same_chap))

    print("\n" + "=" * 78)
    print("候选对拆分与相似度分位")
    print("=" * 78)
    print(f"  {'类别':22s} {'对数':>7}  {'50%':>6} {'75%':>6} {'90%':>6} {'95%':>6} {'99%':>6} {'max':>6}")
    for name, arr in (
        ("跨笔记", cross),
        ("同笔记·跨章节", same_chap_x),
        ("同笔记·同章节", same_chap),
    ):
        print(f"  {name:22s} {len(arr):7d}  {pct(arr,50):6.3f} {pct(arr,75):6.3f} "
              f"{pct(arr,90):6.3f} {pct(arr,95):6.3f} {pct(arr,99):6.3f} "
              f"{(float(arr.max()) if len(arr) else float('nan')):6.3f}")

    # ── 各组的可建边 / 实际新建 / 覆盖 ──
    print("\n" + "=" * 78)
    print("各规则下的边数与覆盖（三个数并列，不合并 —— 见文件头）")
    print("=" * 78)
    print(f"  {'组':2s} {'规则':38s} {'可建边':>7} {'实际新建':>8} {'覆盖卡片':>9}")
    eligible_by_group: Dict[str, Set[Tuple[int, int]]] = {}
    for gid, label, spec, _n in GROUPS:
        el = eligible_pairs(sim, cards, spec)
        eligible_by_group[gid] = el
        new = {p for p in el if tuple(sorted((cards[p[0]]["id"], cards[p[1]]["id"]))) not in existing}
        covered = {i for p in new for i in p}
        print(f"  {gid:2s} {label:38s} {len(el):7d} {len(new):8d} {len(covered):5d}/{len(cards)}")

    # ── 抽样 ──
    rng = random.Random(SAMPLE_SEED)
    sampled: Dict[str, List[Tuple[int, int]]] = {}
    for gid, _label, _spec, n in GROUPS:
        sampled[gid] = sample_group(eligible_by_group[gid], sim, cards, n, rng)

    md = render_sample_markdown(sampled, sim, cards)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"\n抽样表已写入：{args.out}")

    if args.emit_labels:
        template = {f"{gid}:{i}": None for gid, ps in sampled.items() for i in range(len(ps))}
        Path(args.emit_labels).write_text(
            json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"标注模板已写入：{args.emit_labels}（把 null 改成 true / false）")

    if args.labels:
        labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
        return precision_report(labels, sampled)

    if not args.out:
        print("\n" + md)

    return 0


if __name__ == "__main__":
    sys.exit(main())
