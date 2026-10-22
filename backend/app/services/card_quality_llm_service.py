# -*- coding: utf-8 -*-
"""A4 的 **LLM 语义打分**半边（规则版之外）

## 为什么需要它 —— 规则版能做什么、不能做什么

规则版（`card_quality_service.py`）判的是**形态**：内容太短、内容只是把标题
重说一遍、元信息占位……实测在真库 3469 张卡里标记了 **1135 张（32.7%）**。

但"短"不等于"差"。一张 25 字的卡可能是精炼的结论，也可能是残缺的碎片 ——
规则分不出这两种。反过来，一张 200 字的卡可能通篇是原文摘抄、没有可复习的
知识点，规则也看不出来。**这就是 LLM 打分要补的那一块**：判语义，不判形态。

## 判据（三个维度，各 0–100）

| 维度 | 问的问题 | 低分的含义 |
|---|---|---|
| `info_score` | 这张卡有没有**可复习的信息量**？ | 是空话 / 原文摘抄 / 无结论 |
| `self_contained_score` | **脱离原文**能不能看懂？ | 满篇"上述""该值""见表 3" |
| `quizzable_score` | 能不能**据此出一道有区分度的题**？ | 只能出"是什么"式的死记题 |

## 两个设计决定

1. **批量（默认 10 张/次）**：3469 张单张调 = 3469 次；批量 = 347 次。
   实测口径下 tokens 从 ~1.6M 降到 ~0.6M。批内顺序由调用方保证。
2. **解析失败逐张回退**：整批 JSON 解析不了时，把该批**拆成单张重试一次**。
   仍失败则记 `parse_failed` 并继续 —— 一次坏响应不该中断整轮 347 次调用
   （那会让已经花掉的额度白费）。

## 明确不做

**不入库、不删、不合并**（计划 §5 拍板 6）：结果只进报告。
入库要改 schema，而"分数被当权威"是产品语义变更，得另开一轮。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

#: 默认批量大小。
#:
#: ⚠️ 原本是 10，实测在 `max_tokens=2048` 下第 10 批就被截断 ⇒ 改成 **8**。
#: 现在 `max_tokens` 已提到 4096，10 大概也够，但 8 有余量 ——
#: 截断的代价是**10 倍调用**（逐张回退），不值得为省 20% 请求数去赌。
DEFAULT_BATCH_SIZE = 8

#: 三个维度的合法键（少一个就判该卡解析失败，不静默补 0）
SCORE_KEYS = ("info_score", "self_contained_score", "quizzable_score")

#: 建议动作的合法取值 —— 收窄成枚举，避免模型自由发挥出无法统计的字符串
ACTIONS = ("keep", "rewrite", "merge_candidate", "delete_candidate")

SYSTEM_PROMPT = """你是知识卡片的质量评审员。用户会把一批从技术文档里抽取的知识卡片给你，
你要逐张打分，指出它作为**复习材料**的可用性。

## 三个维度（各 0–100 整数）

1. `info_score` —— 有没有可复习的**信息量**？
   - 90–100：是一个完整、准确的知识点
   - 60–89：有信息但偏薄，或掺了无关内容
   - 30–59：基本是原文摘抄 / 空泛表述，复习价值低
   - 0–29：没有信息（"如下图所示""本节介绍了…"这类）

2. `self_contained_score` —— **脱离原文**能不能看懂？
   - 90–100：自足，不需要原文
   - 60–89：小部分指代不明（"该参数"但上文没说是哪个）
   - 30–59：多处依赖原文（"上述步骤""见表 3""如前所述"）
   - 0–29：完全看不懂在说什么

3. `quizzable_score` —— 能不能据此出**有区分度**的题？
   - 90–100：能出理解题 / 应用题
   - 60–89：能出像样的记忆题
   - 30–59：只能出"是什么"式的死记题
   - 0–29：出不了题（没有可问的点）

## 输出格式（**严格 JSON，不要 markdown 代码块，不要解释文字**）

```json
{"results": [
  {"card_id": "<原样回填>", "info_score": 0, "self_contained_score": 0,
   "quizzable_score": 0, "reason": "≤40 字，说清扣分点", "suggested_action": "keep"}
]}
```

`suggested_action` 只能取这四个值之一：
- `keep` —— 不用动
- `rewrite` —— 内容有价值但表述有问题，值得重写
- `merge_candidate` —— 与同批其它卡高度重复，可考虑合并（**只标记，不合并**）
- `delete_candidate` —— 没有保留价值（**只标记，不删除**）

必须对输入的**每一张**卡都返回一条结果，`card_id` 原样回填。"""


@dataclass
class CardScore:
    """一张卡的 LLM 打分结果"""

    card_id: str
    info_score: int
    self_contained_score: int
    quizzable_score: int
    reason: str
    suggested_action: str
    #: 来源批次序号（排查"哪一批的响应坏了"用）
    batch_index: int = -1

    @property
    def average(self) -> float:
        """三维均分 —— 报告里排序用"""
        return round(
            (self.info_score + self.self_contained_score + self.quizzable_score) / 3, 1
        )


@dataclass
class ScoreBatchResult:
    """一次批量打分的结果 + 记账"""

    scores: List[CardScore] = field(default_factory=list)
    #: 解析失败、最终也没拿到分数的 card_id
    parse_failed: List[str] = field(default_factory=list)
    calls: int = 0
    total_tokens: int = 0
    #: 命中缓存的调用数（第二次跑同一批是免费的）
    cached_calls: int = 0


def _build_user_prompt(cards: Sequence[Dict[str, Any]]) -> str:
    """把一批卡拼成用户消息

    ⚠️ `card_id` **必须**出现在提示里，否则模型无从回填 —— 而回填是
    "分数对得上哪张卡"的唯一依据。少了它整批结果只能丢掉。
    """
    lines = [f"共 {len(cards)} 张卡片：", ""]
    for i, c in enumerate(cards, 1):
        lines.append(f"### 卡片 {i}")
        lines.append(f"card_id: {c.get('id', '')}")
        lines.append(f"类型: {c.get('card_type', '')}")
        lines.append(f"标题: {c.get('title', '')}")
        content = (c.get("content") or "").strip()
        lines.append(f"内容: {content if content else '（空）'}")
        lines.append("")
    return "\n".join(lines)


def _coerce_int(value: Any, lo: int = 0, hi: int = 100) -> Optional[int]:
    """把模型给的分数收成合法整数；收不了就返回 None（**不默认成 0**）

    ⚠️ 不默认成 0 是刻意的：0 分意味着"这张卡毫无价值"，而"解析不出来"
    是**我们这边**的问题。把后者写成前者会污染报告、并可能诱导删除。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(lo, min(hi, int(value)))
    if isinstance(value, str):
        try:
            return max(lo, min(hi, int(float(value.strip()))))
        except ValueError:
            return None
    return None


def parse_batch_response(raw: str, expected_ids: Sequence[str], batch_index: int) -> List[CardScore]:
    """解析一批响应；只返回**三个分数都拿得到**的那些卡

    容错点：
    - 去掉 markdown 代码块围栏（模型经常无视"不要代码块"的要求）；
    - 接受 `{"results": [...]}` 与裸 `[...]` 两种形状；
    - 只认 `expected_ids` 里的 id（模型偶尔会编 id 或漏 id）。
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        # 去掉 ```json ... ``` 围栏
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("LLM 打分响应不是合法 JSON（批 %s），前 200 字：%s", batch_index, text[:200])
        return []

    items = data.get("results") if isinstance(data, dict) else data
    if not isinstance(items, list):
        logger.warning("LLM 打分响应形状不认识（批 %s）：%s", batch_index, type(data))
        return []

    allowed = set(expected_ids)
    out: List[CardScore] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("card_id") or "").strip()
        if cid not in allowed:
            continue
        scores = [_coerce_int(item.get(k)) for k in SCORE_KEYS]
        if any(s is None for s in scores):
            logger.debug("卡片 %s 有三个分数缺失，跳过", cid)
            continue
        action = str(item.get("suggested_action") or "keep").strip()
        if action not in ACTIONS:
            action = "keep"
        out.append(
            CardScore(
                card_id=cid,
                info_score=scores[0],
                self_contained_score=scores[1],
                quizzable_score=scores[2],
                reason=str(item.get("reason") or "")[:120],
                suggested_action=action,
                batch_index=batch_index,
            )
        )
    return out


async def score_cards_batch(
    cards: Sequence[Dict[str, Any]],
    *,
    gateway: Any,
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_progress: Optional[Any] = None,
) -> ScoreBatchResult:
    """给一批卡打 LLM 语义分

    Args:
        cards: 每项至少含 `id` / `title` / `content` / `card_type`
        gateway: `LLMGateway` 实例（**注入**而不是内部构造 —— 测试可替换）
        batch_size: 每次调用带几张卡
        on_progress: 可选回调 `(done, total, result)`，给脚本打印进度

    Returns:
        `ScoreBatchResult`：分数 + 解析失败清单 + token 记账
    """
    result = ScoreBatchResult()
    total = len(cards)

    for start in range(0, total, batch_size):
        chunk = list(cards[start : start + batch_size])
        batch_index = start // batch_size
        expected = [str(c.get("id")) for c in chunk]

        got = await _score_one_call(chunk, expected, batch_index, gateway, result)

        # 解析失败逐张回退：整批只拿回一部分时，缺的那些单独再问一次
        missing = [c for c in chunk if str(c.get("id")) not in {s.card_id for s in got}]
        if missing:
            logger.info(
                "批 %s 有 %d/%d 张没拿到分数，逐张回退", batch_index, len(missing), len(chunk)
            )
            for card in missing:
                single = await _score_one_call(
                    [card], [str(card.get("id"))], batch_index, gateway, result
                )
                got.extend(single)

        result.scores.extend(got)
        for card in chunk:
            if str(card.get("id")) not in {s.card_id for s in result.scores}:
                result.parse_failed.append(str(card.get("id")))

        if on_progress is not None:
            on_progress(min(start + batch_size, total), total, result)

    return result


async def _score_one_call(
    chunk: Sequence[Dict[str, Any]],
    expected: Sequence[str],
    batch_index: int,
    gateway: Any,
    result: ScoreBatchResult,
) -> List[CardScore]:
    """发一次调用并解析（不含回退逻辑，回退在调用方）"""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_prompt(chunk)},
    ]
    try:
        resp = await gateway.chat_detailed(
            messages,
            temperature=0.2,  # 打分要稳，不要创意
            # ⚠️ 2048 **不够**：实测第 10 批就被截断（10 条结果 × 3 个分数 +
            #    中文理由 ≈ 顶到上限）⇒ JSON 不完整 ⇒ 触发逐张回退 ⇒
            #    那一批从 1 次调用变成 11 次。截断的代价是 10 倍调用，
            #    余量的代价只是略多的请求数。
            max_tokens=4096,
            response_format={"type": "json_object"},
            scene="card_quality_score",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("LLM 打分调用失败（批 %s，%d 张）：%s", batch_index, len(chunk), e)
        return []

    result.calls += 1
    usage = resp.get("usage") or {}
    result.total_tokens += int(usage.get("total_tokens") or 0)
    if usage.get("cached") or resp.get("cached"):
        result.cached_calls += 1

    return parse_batch_response(resp.get("content") or "", expected, batch_index)


__all__ = [
    "ACTIONS",
    "DEFAULT_BATCH_SIZE",
    "SCORE_KEYS",
    "SYSTEM_PROMPT",
    "CardScore",
    "ScoreBatchResult",
    "parse_batch_response",
    "score_cards_batch",
]
