"""健壮 JSON 解析（见 docs/decisions.md#F-33）

应对 LLM 输出「截断 / 围栏包裹 / 尾缀杂文」三类问题而设的容错解析设施。
"""

import json
import re
from typing import Any, Dict, List, Optional, Tuple


# ===========================================================================
# 健壮 JSON 解析（应对 LLM 输出「截断 / 围栏包裹 / 尾缀杂文」三类问题，见 docs/decisions.md#F-33）
# ===========================================================================

def strip_json_fences(text: str) -> str:
    """
    剥离 LLM 输出中的 markdown 代码围栏（```json ... ``` / ``` ... ```）

    实测：模型即使指定 response_format=json_object，也可能把结果包在
    代码围栏里，此时 json.loads 直接失败。围栏剥离必须在解析前完成，见 docs/decisions.md#F-33。
    """
    s = (text or "").strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", s)
    if m:
        s = m.group(1).strip()
    return s


def _first_json_pos(text: str) -> Optional[int]:
    """查找首个 JSON 容器起始位置（跳过前导散文）"""
    for i, c in enumerate(text):
        if c in "{[":
            return i
    return None


def _salvage_json_prefix(text: str, start: int) -> Tuple[Optional[Any], int]:
    """
    截断抢救：从截断的 JSON 开头向后扫描，找出「已生成完整、只需补闭合括号」的最长前缀。

    原理：LLM 截断（finish_reason=length）后文本通常是：
      {"chapters": [{...}, {...},        ← 数组未闭合
      {"questions": [{...}, {...}]       ← 缺最外层 }
      [ {..}, {..},                      ← 数组未闭合
    该扫描在字符串感知（忽略引号内 {,}，处理 \\ 转义）的前提下，记录栈深度 ≤ 2 的
    浅层闭合点，然后依次尝试「原文 + 补齐闭合括号」能否被 json.loads 解析，
    取最长可解析者。若整体不可解析（在字符串中间截断），返回最后一个可闭合前缀。

    Args:
        text: 截断的 JSON 文本（可能含围栏/杂文）
        start: JSON 容器起始下标

    Returns:
        (解析出的数据, 结束下标)；抢救失败返回 (None, -1)
    """
    stack: List[str] = []
    snapshots: List[Tuple[int, Tuple[str, ...]]] = []  # (下标, 栈快照)
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "{[":
            stack.append(c)
        elif c in "}]":
            if not stack:
                break  # 多余闭合，视为截断边界
            stack.pop()
            # 深度 ≤ 2 的浅层闭合点：截断通常发生在这里
            if len(stack) <= 2:
                snapshots.append((i, tuple(stack)))

    def try_load(snippet: str) -> Optional[Any]:
        try:
            return json.loads(snippet)
        except json.JSONDecodeError:
            return None

    tried = set()
    # 从最长到最短尝试
    for idx, stack_snap in sorted(snapshots, key=lambda x: -x[0]):
        snippet = text[start:idx + 1]
        closers = "".join("]" if o == "[" else "}" for o in reversed(stack_snap))
        for candidate in (snippet, snippet + closers, snippet + closers * 2):
            if candidate in tried:
                continue
            tried.add(candidate)
            data = try_load(candidate)
            if data is not None:
                return data, idx
    return None, -1


def parse_json_tolerant(text: str) -> Tuple[Optional[Any], Dict[str, Any]]:
    """
    健壮 JSON 解析（见 docs/decisions.md#F-33）

    按层级尝试，逐级提升容错能力，尽量挽回模型输出：
    1. 直接 json.loads（围栏剥离后）
    2. raw_decode 首个容器——处理「JSON + 尾缀杂文」（截断/多行注释外的常见情况）
    3. 截断抢救——补齐闭合括号，取最长可解析前缀（部分数据）

    Args:
        text: LLM 输出原文

    Returns:
        (data, info)：
            data 可能为部分数据（截断抢救）或 None（彻底失败）
            info: {"status": "ok" | "ok_with_tail" | "partial" | "failed",
                   "detail": str}
    """
    if not text or not text.strip():
        return None, {"status": "empty", "detail": "empty response"}

    s = strip_json_fences(text)

    # 1) 直接解析
    try:
        return json.loads(s), {"status": "ok", "detail": ""}
    except json.JSONDecodeError:
        pass

    # 2) JSON + 尾缀杂文 / 前导杂文
    first = _first_json_pos(s)
    if first is not None:
        try:
            data, _end = json.JSONDecoder().raw_decode(s, first)
            return data, {"status": "ok_with_tail", "detail": "json with trailing prose"}
        except json.JSONDecodeError:
            pass

        # 3) 截断抢救
        salvaged, end = _salvage_json_prefix(s, first)
        if salvaged is not None:
            return salvaged, {
                "status": "partial",
                "detail": f"truncated json salvaged up to offset {end}",
            }

    return None, {"status": "failed", "detail": f"unparseable: {s[:200]}"}