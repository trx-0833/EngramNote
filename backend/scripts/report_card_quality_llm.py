# -*- coding: utf-8 -*-
"""A4 的 LLM 语义打分报告（**只读真库**，只标记不删）

## 与规则版的关系

规则版（`scripts/report_card_quality.py`）判**形态**，LLM 版判**语义**。
两者不是替代关系 —— 报告里有一节专门做**交叉验证**：两版都标记的、
只有一版标记的，各自抽样列出。一致率本身就是结论的一部分：

- 一致率高 ⇒ 规则版够用，LLM 那 0.6M tokens 可以省下来；
- 一致率低 ⇒ 说明"短"与"差"确实是两件事，规则版漏判/误判了多少有数字。

## 只读保证

`sqlite3.connect(f"file:{db}?mode=ro", uri=True)` —— **只读连接**，
连误写都不可能。本脚本不执行任何 UPDATE/DELETE（拍板 6：只标记不删）。

## 用法

    python scripts/report_card_quality_llm.py --limit 50          # 小样
    python scripts/report_card_quality_llm.py --out docs/card-quality-llm-report-2026-10-22.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.services.card_quality_llm_service import (  # noqa: E402
    DEFAULT_BATCH_SIZE,
    score_cards_batch,
)
from app.services.card_quality_service import score_card  # noqa: E402

DEFAULT_DB = str(BACKEND_ROOT / "data" / "db" / "engramnote.db")

#: LLM 均分低于这个值算"低分卡"（进清单）。60 是提示词里"有信息但偏薄"的上界
LOW_SCORE_THRESHOLD = 60.0


def load_cards(db_path: str, user_id: str) -> List[Dict[str, Any]]:
    """只读加载卡片（取卡片最多的那个用户，除非显式指定）"""
    import sqlite3

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        if not user_id:
            user_id = con.execute(
                "SELECT user_id FROM knowledge_cards GROUP BY user_id ORDER BY count(*) DESC LIMIT 1"
            ).fetchone()[0]
        rows = con.execute(
            """SELECT c.id, c.title, c.content, c.card_type, c.note_id, n.title AS note_title
               FROM knowledge_cards c LEFT JOIN notes n ON n.id = c.note_id
               WHERE c.user_id = ? ORDER BY c.id""",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def _rule_flags(cards: List[Dict[str, Any]]) -> Dict[str, Any]:
    """跑规则版，拿到每张卡的命中规则（用于交叉验证）"""
    out = {}
    for c in cards:
        q = score_card(c)
        out[c["id"]] = {"flagged": bool(q.reasons), "reasons": q.reasons, "severe": q.severe}
    return out


def build_report(
    cards: List[Dict[str, Any]],
    scores: List[Any],
    rule: Dict[str, Any],
    *,
    parse_failed: List[str],
    usage: Dict[str, Any],
    top: int,
) -> Dict[str, Any]:
    by_id = {c["id"]: c for c in cards}
    score_by_id = {s.card_id: s for s in scores}

    low = sorted(
        (s for s in scores if s.average < LOW_SCORE_THRESHOLD),
        key=lambda s: s.average,
    )

    # 交叉验证：两版各自标记的集合
    llm_low = {s.card_id for s in low}
    rule_flag = {cid for cid, v in rule.items() if v["flagged"]}
    both = llm_low & rule_flag
    only_llm = llm_low - rule_flag
    only_rule = rule_flag - llm_low

    def _row(cid: str, extra: str = "") -> str:
        c = by_id.get(cid, {})
        s = score_by_id.get(cid)
        title = (c.get("title") or "")[:34]
        content = (c.get("content") or "").replace("\n", " ")[:60]
        if s:
            sc = f"{s.average:5.1f} ({s.info_score}/{s.self_contained_score}/{s.quizzable_score})"
            reason = s.reason[:40]
        else:
            sc, reason = "  —  ", "（无 LLM 分）"
        return f"| {title} | {sc} | {reason} | {content} |{extra}"

    return {
        "total": len(cards),
        "scored": len(scores),
        "parse_failed": parse_failed,
        "low_count": len(low),
        "low_ratio": round(len(low) / len(scores) * 100, 1) if scores else 0.0,
        "by_action": dict(Counter(s.suggested_action for s in scores)),
        "avg_by_dim": {
            k: round(sum(getattr(s, k) for s in scores) / len(scores), 1) if scores else 0.0
            for k in ("info_score", "self_contained_score", "quizzable_score")
        },
        "cross": {
            "rule_flagged": len(rule_flag),
            "llm_low": len(llm_low),
            "both": len(both),
            "only_llm": len(only_llm),
            "only_rule": len(only_rule),
            # 以规则版为基准的召回率：规则标记里有多少 LLM 也认为差
            "agree_on_rule_flagged": round(len(both) / len(rule_flag) * 100, 1) if rule_flag else 0.0,
        },
        "usage": usage,
        "top_low": low[:top],
        "only_llm_sample": [score_by_id[c] for c in list(only_llm)[:15] if c in score_by_id],
        "only_rule_sample": list(only_rule)[:15],
        "_by_id": by_id,
        "_score_by_id": score_by_id,
        "_row": _row,
    }


def render_markdown(rep: Dict[str, Any], *, top: int) -> str:
    row = rep["_row"]
    by_id = rep["_by_id"]
    u = rep["usage"]
    c = rep["cross"]

    lines = [
        "# A4 · 卡片质量 LLM 语义打分报告",
        "",
        "> **状态**：生成于 2026-10-22，A4 的 LLM 半边（规则版见 `docs/card-quality-report.md`）。",
        "> **只读**：本报告由 `backend/scripts/report_card_quality_llm.py` 生成，数据库以 `mode=ro` 打开。",
        "> **只标记不删**：`suggested_action` 是**建议**，没有任何删除或合并被执行。",
        "",
        "## 1. 总览",
        "",
        f"- 卡片总数：**{rep['total']}**，拿到 LLM 分：**{rep['scored']}**"
        + (f"，解析失败：**{len(rep['parse_failed'])}**" if rep["parse_failed"] else ""),
        f"- 低分卡（三维均分 < {LOW_SCORE_THRESHOLD:.0f}）：**{rep['low_count']}** 张"
        f"（占已打分 {rep['low_ratio']}%）",
        f"- 三维均分：信息量 **{rep['avg_by_dim']['info_score']}** / "
        f"自足性 **{rep['avg_by_dim']['self_contained_score']}** / "
        f"可出题性 **{rep['avg_by_dim']['quizzable_score']}**",
        f"- 建议动作分布：{rep['by_action']}",
        f"- 额度：**{u.get('calls', 0)} 次调用 / {u.get('tokens', 0)} tokens**"
        f"（缓存命中 {u.get('cached_calls', 0)} 次）",
        "",
        "## 2. ★ 与规则版的交叉验证",
        "",
        "规则版判**形态**（太短 / 标题回声 / 元信息占位），LLM 版判**语义**。",
        "两者不一致的部分正是「LLM 打分值不值这 0.6M tokens」的答案。",
        "",
        "| 口径 | 张数 |",
        "|---|---|",
        f"| 规则版标记 | {c['rule_flagged']} |",
        f"| LLM 判低分 | {c['llm_low']} |",
        f"| **两版都标记** | **{c['both']}** |",
        f"| 只有 LLM 标记（规则漏判） | {c['only_llm']} |",
        f"| 只有规则标记（LLM 认为还行） | {c['only_rule']} |",
        f"| **一致率**（规则标记里 LLM 也判差的比例） | **{c['agree_on_rule_flagged']}%** |",
        "",
    ]

    if rep["only_llm_sample"]:
        lines += [
            f"### 2.1 规则版**漏判**的（只有 LLM 判低分，抽 {len(rep['only_llm_sample'])} 张）",
            "",
            "这些卡形态上没问题（够长、不是标题回声），但 LLM 认为没有可复习的信息量 ——",
            "**这正是规则版做不到的那一块**。",
            "",
            "| 标题 | LLM 均分（信息/自足/可出题） | LLM 理由 | 内容摘要 |",
            "|---|---|---|---|",
        ]
        lines += [row(s.card_id) for s in rep["only_llm_sample"]]
        lines.append("")

    if rep["only_rule_sample"]:
        lines += [
            f"### 2.2 规则版标记但 LLM 认为**还行**的（抽 {len(rep['only_rule_sample'])} 张）",
            "",
            "这些卡「短」，但 LLM 认为信息量足够 —— 说明**「短」不等于「差」**，",
            "规则版的 32.7% 标记率里有误判。",
            "",
            "| 标题 | 规则命中 | 内容摘要 |",
            "|---|---|---|",
        ]
        for cid in rep["only_rule_sample"]:
            cd = by_id.get(cid, {})
            lines.append(
                f"| {(cd.get('title') or '')[:34]} | （规则版标记） | "
                f"{(cd.get('content') or '').replace(chr(10), ' ')[:60]} |"
            )
        lines.append("")

    lines += [
        f"## 3. 低分卡清单（前 {min(top, len(rep['top_low']))} 张，按均分升序）",
        "",
        "| 标题 | LLM 均分（信息/自足/可出题） | LLM 理由 | 内容摘要 |",
        "|---|---|---|---|",
    ]
    lines += [row(s.card_id) for s in rep["top_low"]]
    lines += [
        "",
        "## 4. 明确不做的事",
        "",
        "- **不删、不合并**：`suggested_action` 里的 `delete_candidate` / `merge_candidate`",
        "  只是**建议**。任何删除动作按计划 §5 拍板 6 另开一轮单独批。",
        "- **分数不入库**：要改 schema，且「分数被当权威」属产品语义变更。",
        "",
    ]
    return "\n".join(lines)


def _precheck_output_paths(args: argparse.Namespace) -> None:
    """在**发第一次 LLM 调用之前**校验输出路径可写

    ⚠️ 这个校验必须放在最前面：脚本会跑几百次调用，如果等到最后写文件才发现
    目录不存在，那几百次调用的额度就白花了。实测踩过一次 ——
    cwd 是 `backend/`，而文档在仓库根的 `docs/`，写成 `docs/...` 会指向
    不存在的 `backend/docs/`。
    """
    for label, raw in (("--out", args.out), ("--json", args.json)):
        if not raw:
            continue
        target = Path(raw)
        parent = target.parent if str(target.parent) else Path(".")
        if not parent.exists():
            raise SystemExit(
                f"[a4-llm] 拒绝开跑：{label} 的目录不存在 —— {parent.resolve()}\n"
                f"        本脚本的 cwd 是 backend/，而项目文档在仓库根的 docs/。\n"
                f"        要写到项目文档请用：--out ../docs/<文件名>.md"
            )


async def run(args: argparse.Namespace) -> int:
    from app.services.llm.gateway import LLMGateway

    # ⚠️ 放在读库之前：路径错了就一秒都不要浪费
    _precheck_output_paths(args)

    cards = load_cards(args.db, args.user_id)
    if args.limit:
        cards = cards[: args.limit]
    print(f"[a4-llm] 加载 {len(cards)} 张卡（只读）", flush=True)

    rule = _rule_flags(cards)
    print(f"[a4-llm] 规则版标记 {sum(1 for v in rule.values() if v['flagged'])} 张", flush=True)

    gateway = LLMGateway.from_settings()

    def _dump(res: Any) -> None:
        """增量落盘：每批写一次，中断不丢结果"""
        if not args.json:
            return
        payload = {
            "scored": len(res.scores),
            "calls": res.calls,
            "tokens": res.total_tokens,
            "parse_failed": res.parse_failed,
            "scores": [
                {
                    "card_id": x.card_id,
                    "info_score": x.info_score,
                    "self_contained_score": x.self_contained_score,
                    "quizzable_score": x.quizzable_score,
                    "reason": x.reason,
                    "suggested_action": x.suggested_action,
                }
                for x in res.scores
            ],
        }
        Path(args.json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def progress(done: int, total: int, res: Any) -> None:
        print(
            f"[a4-llm] {done}/{total} 张  调用={res.calls} tokens={res.total_tokens}",
            flush=True,
        )
        # ⚠️ 每批都落盘：上一轮 kill 掉时 1808 条结果全在内存里、一条没留下
        _dump(res)

    res = await score_cards_batch(
        cards, gateway=gateway, batch_size=args.batch_size, on_progress=progress
    )
    print(
        f"[a4-llm] 完成：{len(res.scores)} 张有分，{len(res.parse_failed)} 张解析失败，"
        f"{res.calls} 次调用 / {res.total_tokens} tokens",
        flush=True,
    )

    rep = build_report(
        cards,
        res.scores,
        rule,
        parse_failed=res.parse_failed,
        usage={
            "calls": res.calls,
            "tokens": res.total_tokens,
            "cached_calls": res.cached_calls,
        },
        top=args.top,
    )
    md = render_markdown(rep, top=args.top)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"[a4-llm] 报告已写入 {args.out}", flush=True)
    if args.json:
        # 最终文件 = 报告摘要 + 逐卡分数（与增量落盘同格式，多一个 report 字段）
        payload = json.loads(Path(args.json).read_text(encoding="utf-8"))
        # ⚠️ 只留**可序列化**的汇总字段：	op_low / only_llm_sample 里是
        #    CardScore 对象，直接塞进 JSON 会 TypeError（实测）。
        #    逐卡分数已经在 scores 里了，重复一遍没有意义。
        _skip = {'top_low', 'only_llm_sample', 'only_rule_sample'}
        payload['report'] = {
            k: v for k, v in rep.items() if not k.startswith('_') and k not in _skip
        }
        Path(args.json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[a4-llm] JSON 已写入 {args.json}", flush=True)
    if not args.out and not args.json:
        print(md)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="A4 卡片质量 LLM 语义打分（只读真库）")
    ap.add_argument("--db", default=DEFAULT_DB, help="数据库路径（**只读**打开）")
    ap.add_argument("--user-id", default="", help="用户 ID（默认取卡片最多的那个）")
    ap.add_argument("--limit", type=int, default=0, help="只打前 N 张（小样用，0=全部）")
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="每次调用带几张卡")
    ap.add_argument("--top", type=int, default=40, help="报告里列多少张低分卡")
    ap.add_argument("--out", default="", help="markdown 输出路径")
    ap.add_argument("--json", default="", help="JSON 输出路径（便于后续统计）")
    args = ap.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
