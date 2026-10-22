# -*- coding: utf-8 -*-
"""B5 ②：AI 对比原始资料，指出**遗漏概念**与**理解偏差**

## 它要回答的问题

理解管道从一篇文档里抽卡片，抽完之后用户面对的是**产物**，看不到
"原文里还有什么没被抽出来"。B5 ② 补的就是这个视角：

1. `missing_concepts` —— 原文里有、卡片里**没有**的重要概念；
2. `misunderstandings` —— 卡片**说错了**或说偏了的地方（附原文依据）。

这与 A4 的区别：A4 判"这张卡自己好不好"，B5 判"这批卡相对**这篇原文**缺了什么"。
前者不需要原文，后者必须两样都有。

## 两个设计决定

1. **幂等靠 `llm_cache`，不落新表**。缓存键含提示词全文，而提示词含卡片内容与
   原文片段 ⇒ 卡片没变则第二次请求**零额度**。响应里带 `cached` 让前端明示
   "本次未消耗额度"。落新表要改 schema，而这份结论的时效性跟着卡片走 ——
   卡片一变结论就过期，存下来反而要处理失效。
2. **过长原文分段评估再合并**。分段阈值按字符数（不是 token）估算，
   取保守值，宁可多切一段也不要被静默截断 —— 截断会让"遗漏"凭空变多。

## 明确不做

- **不改任何卡片**：只产出清单。改卡是用户看着清单自己决定的事。
- **不自动重跑理解**。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

#: 原文分段阈值（字符）。中文 1 字 ≈ 1 token 上下，留足输出预算后取 12000
SEGMENT_CHARS = 12000

#: 每段最多带上多少张卡片标题（卡片多时按顺序截断，避免提示词爆掉）
MAX_CARD_TITLES = 400

SYSTEM_PROMPT = """你是学习材料的审核员。用户给你两样东西：

1. **原始资料**（一篇技术文档的片段）；
2. 从这份资料**已抽取的知识卡片**（标题 + 类型）。

你的任务是对照两者，找出两类问题。**只报有依据的，不要为了凑数而报。**

## 第一类：`missing_concepts`（原文有、卡片里没有的重要概念）

判"重要"的标准：这个概念是理解该段落的**关键**（定义、原理、参数取值、
操作规程、判据阈值），缺了它读者会看不懂或会做错。**不要**把"原文里的
每一句话"都当成概念 —— 举例、过渡语、重复表述不算。

每条给出：
- `name`：概念名（尽量用原文的术语）
- `why_important`：为什么重要（≤50 字）
- `source_quote`：**原文里的依据原句**（≤80 字，必须是原文中真实存在的片段）

## 第二类：`misunderstandings`（卡片说错或说偏了）

只在**确实有问题**时报告，并指明是哪张卡（用它的 card_id）。常见形态：
- 卡片把原文的限定条件丢了（"在 X 情况下"被省略）
- 数值/单位/方向写错
- 把因果说反，或把"建议"说成"必须"

每条给出：
- `card_id`：卡片 id（原样回填）
- `card_title`：卡片标题
- `problem`：问题是什么（≤60 字）
- `source_quote`：原文依据（≤80 字）
- `suggestion`：怎么改（≤50 字）

## 输出格式（**严格 JSON，不要 markdown 代码块，不要解释文字**）

```json
{"missing_concepts": [{"name": "", "why_important": "", "source_quote": ""}],
 "misunderstandings": [{"card_id": "", "card_title": "", "problem": "",
                        "source_quote": "", "suggestion": ""}],
 "coverage_note": "≤80 字，整体覆盖情况的一句话判断"}
```

两类都可以为空数组 —— 如果这段资料被覆盖得确实好，就返回空数组，
**不要编造问题**。"""


@dataclass
class MissingConcept:
    name: str
    why_important: str = ""
    source_quote: str = ""


@dataclass
class Misunderstanding:
    card_id: str
    card_title: str = ""
    problem: str = ""
    source_quote: str = ""
    suggestion: str = ""


@dataclass
class NoteEvalResult:
    missing_concepts: List[MissingConcept] = field(default_factory=list)
    misunderstandings: List[Misunderstanding] = field(default_factory=list)
    coverage_note: str = ""
    #: 实际发了几次调用（分段后 > 1）
    calls: int = 0
    total_tokens: int = 0
    #: 是否**全部**调用都命中缓存（前端据此明示"未消耗额度"）
    all_cached: bool = True
    segments: int = 0
    #: 拿不到原文时的说明（前端要如实告诉用户"没原文可对照"）
    source_unavailable: bool = False


def split_source(text: str, limit: int = SEGMENT_CHARS) -> List[str]:
    """把原文按段落切成不超过 `limit` 字符的片段

    按**空行**切（Markdown 的段落边界），尽量不切断一段话。
    单段本身就超限时硬切 —— 否则那一段永远发不出去。
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    paras = [p for p in text.split("\n\n") if p.strip()]
    out: List[str] = []
    buf: List[str] = []
    size = 0
    for p in paras:
        if len(p) > limit:
            if buf:
                out.append("\n\n".join(buf))
                buf, size = [], 0
            for i in range(0, len(p), limit):
                out.append(p[i : i + limit])
            continue
        if size + len(p) + 2 > limit and buf:
            out.append("\n\n".join(buf))
            buf, size = [], 0
        buf.append(p)
        size += len(p) + 2
    if buf:
        out.append("\n\n".join(buf))
    return out


def _build_user_prompt(segment: str, cards: Sequence[Dict[str, Any]]) -> str:
    titles = []
    for c in cards[:MAX_CARD_TITLES]:
        titles.append(f"- [{c.get('id', '')}] ({c.get('card_type', '')}) {c.get('title', '')}")
    card_block = "\n".join(titles) if titles else "（这篇资料还没有任何卡片）"
    return (
        f"## 原始资料片段\n\n{segment}\n\n"
        f"## 已抽取的卡片（共 {len(cards)} 张，下列出前 {len(titles)} 张）\n\n{card_block}"
    )


def parse_eval_response(raw: str) -> Dict[str, Any]:
    """解析一次评估响应（容错：去代码块围栏、两种形状、字段缺失）"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("笔记评估响应不是合法 JSON，前 200 字：%s", text[:200])
        return {"missing_concepts": [], "misunderstandings": [], "coverage_note": ""}
    if not isinstance(data, dict):
        return {"missing_concepts": [], "misunderstandings": [], "coverage_note": ""}
    return data


async def evaluate_note(
    *,
    source_text: Optional[str],
    cards: Sequence[Dict[str, Any]],
    gateway: Any,
) -> NoteEvalResult:
    """对照原文评估卡片覆盖情况

    Args:
        source_text: 原文（clean 优先，回落 original）；为 None 时**不发调用**，
            直接返回 `source_unavailable=True` —— 没有原文就无从对照，
            发出去只会让模型凭卡片自己编"遗漏"。
        cards: 该笔记的卡片（至少含 id / title / card_type）
        gateway: `LLMGateway` 实例（注入，测试可替换）
    """
    result = NoteEvalResult()

    if not source_text or not source_text.strip():
        result.source_unavailable = True
        return result

    segments = split_source(source_text)
    result.segments = len(segments)

    for idx, seg in enumerate(segments):
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(seg, cards)},
        ]
        try:
            resp = await gateway.chat_detailed(
                messages,
                temperature=0.2,
                max_tokens=3000,
                response_format={"type": "json_object"},
                scene="note_eval",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("笔记评估第 %d/%d 段调用失败：%s", idx + 1, len(segments), e)
            continue

        result.calls += 1
        usage = resp.get("usage") or {}
        result.total_tokens += int(usage.get("total_tokens") or 0)
        if not (usage.get("cached") or resp.get("cached")):
            result.all_cached = False

        data = parse_eval_response(resp.get("content") or "")
        for m in data.get("missing_concepts") or []:
            if not isinstance(m, dict) or not (m.get("name") or "").strip():
                continue
            result.missing_concepts.append(
                MissingConcept(
                    name=str(m.get("name"))[:120],
                    why_important=str(m.get("why_important") or "")[:200],
                    source_quote=str(m.get("source_quote") or "")[:300],
                )
            )
        for m in data.get("misunderstandings") or []:
            if not isinstance(m, dict):
                continue
            result.misunderstandings.append(
                Misunderstanding(
                    card_id=str(m.get("card_id") or ""),
                    card_title=str(m.get("card_title") or "")[:120],
                    problem=str(m.get("problem") or "")[:200],
                    source_quote=str(m.get("source_quote") or "")[:300],
                    suggestion=str(m.get("suggestion") or "")[:200],
                )
            )
        note = (data.get("coverage_note") or "").strip()
        if note and not result.coverage_note:
            result.coverage_note = note[:200]

    return result


__all__ = [
    "MAX_CARD_TITLES",
    "SEGMENT_CHARS",
    "SYSTEM_PROMPT",
    "Misunderstanding",
    "MissingConcept",
    "NoteEvalResult",
    "evaluate_note",
    "parse_eval_response",
    "split_source",
]
