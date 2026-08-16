# -*- coding: utf-8 -*-
"""
Markdown 结构感知分段器（F-34）
================================

解决"分段提取知识点的局限性"：旧的切分按固定字符数 / 段落硬切，
会把表格行、代码块、列表项、长句从中间截断，导致送入 LLM 的片段
上下文不连贯（"文本截断"问题）。

本模块提供三类能力：
1. split_markdown_blocks(text) —— 把 Markdown 切分为"结构完整的最小单元"（块）：
   标题 / 段落 / 列表 / 表格 / 代码块 / 引用 / 分隔线。块是原子单元，
   不再从中间截断。
2. make_segments(blocks, limit) —— 把块贪心打包成 ≤limit 的分段：
   - 块能完整放下 → 放入当前段
   - 放不下 → 新起一段，**整块跟着走**（绝不会在块中间切，除非该块本身超限）
   - 单个块超限 → 在"安全边界"内拆分（表格按行、列表按项、段落按句、代码按行），
     **未消费的尾部成为下一段的开头续接**（即"把这一段放到前文"），
     若续接长度仍超限则顺延到后续批次处理（即"放到第二次上传再处理"）
3. truncate_to_complete_blocks(text, limit) —— 返回 (完整块前缀, 剩余部分)，
   供"整段放不下的防御性截断"使用，保证不切破块边界。

设计决策：
- 纯标准库（re），不依赖第三方 markdown 解析库。原因：
  markdown 解析库（如 markdown-it-py / mistune）提供的是 AST/标记流，并不能
  直接给出"在字符预算内上下文完整的分段"；而本场景的分块规则（标题/围栏/表格
  行/列表项/句子）用确定性行扫描即可精准实现，且零依赖、可控、可测试。
- 块定义尽量宽松（启发式），与该 app 实际生产的 Markdown（MinerU 转换 / 手写
  笔记）匹配：表格行以 | 开头、代码围栏 ```、清单 - * 1. 等。
"""

import re
from typing import List, Tuple

# ---- 块类型正则 ----
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
FENCE_RE = re.compile(r"^(`{3,}|~{3,})")
LIST_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")
QUOTE_RE = re.compile(r"^>\s?")
HR_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
TABLE_ROW_RE = re.compile(r"^\s*\|")
# 句末分隔（中文/英文标点；标点后有无空格均识别为句界，避免"句甲。句甲。"退化为字符硬切）
_SENTENCE_RE = re.compile(r"(?<=[。！？!?；;])\s*(?=\S)")
# 列表项分隔：独立行首的列表标记
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")


def split_markdown_blocks(text: str) -> List[str]:
    """把 Markdown 文本切成结构完整的块（原子单元），返回块列表（不含空块）"""
    if not text:
        return []
    lines = (text or "").split("\n")
    n = len(lines)
    blocks: List[str] = []
    i = 0

    def push(buf: List[str]) -> None:
        s = "\n".join(buf).strip("\n")
        if s.strip():
            blocks.append(s)

    while i < n:
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue

        # 围栏代码块：收集到匹配的结束围栏
        m = FENCE_RE.match(stripped)
        if m:
            fence = m.group(1)
            fence_char = fence[0]
            need = len(fence)
            buf = [lines[i]]
            i += 1
            while i < n:
                buf.append(lines[i])
                inner = lines[i].strip()
                if inner.startswith(fence_char):
                    run = len(re.match(rf"{re.escape(fence_char)}+", inner).group(0))
                    if run >= need:
                        i += 1
                        break
                i += 1
            push(buf)
            continue

        # 标题：单行即块（标题本身是原子）
        if HEADING_RE.match(stripped):
            push([lines[i]])
            i += 1
            continue

        # 引用：连续 > 行（含空行）为一个块
        if QUOTE_RE.match(stripped):
            buf = [lines[i]]
            i += 1
            while i < n:
                s2 = lines[i].strip()
                if QUOTE_RE.match(s2) or not s2:
                    buf.append(lines[i])
                    i += 1
                else:
                    break
            push(buf)
            continue

        # 分隔线
        if HR_RE.match(stripped):
            push([lines[i]])
            i += 1
            continue

        # 表格：连续以 | 开头的行（含表头/分隔行）为一个块
        if TABLE_ROW_RE.match(stripped):
            buf = [lines[i]]
            i += 1
            while i < n and TABLE_ROW_RE.match(lines[i].strip()):
                buf.append(lines[i])
                i += 1
            push(buf)
            continue

        # 列表：连续列表标记行（含缩进续行）为一个块
        if LIST_RE.match(stripped):
            buf = [lines[i]]
            i += 1
            while i < n:
                s2 = lines[i].strip()
                if not s2:
                    break
                if LIST_RE.match(s2) or (lines[i].startswith((" ", "\t"))):
                    buf.append(lines[i])
                    i += 1
                else:
                    break
            push(buf)
            continue

        # 普通段落：连续非特殊非空行
        buf = [lines[i]]
        i += 1
        while i < n:
            s2 = lines[i].strip()
            if not s2:
                break
            if (HEADING_RE.match(s2) or FENCE_RE.match(s2) or QUOTE_RE.match(s2)
                    or HR_RE.match(s2) or TABLE_ROW_RE.match(s2) or LIST_RE.match(s2)):
                break
            buf.append(lines[i])
            i += 1
        push(buf)

    return blocks


def _is_table_block(block: str) -> bool:
    lines = [l for l in block.splitlines() if l.strip()]
    return bool(lines) and all(TABLE_ROW_RE.match(l.strip()) for l in lines)


def _split_table_block(block: str, limit: int) -> List[str]:
    """表格超限：按数据行拆分，每个子表保留表头 + 分隔行"""
    rows = block.splitlines()
    header_rows = [rows[0]]
    # 第二行若为分隔行 |---|---| 则一并保留为表头
    if len(rows) >= 2 and set(rows[1].replace("|", "").replace(" ", "").replace("-", "").replace(":", "")) == set():
        header_rows.append(rows[1])
        data_rows = rows[2:]
    else:
        data_rows = rows[1:]
    chunks: List[str] = []
    cur = list(header_rows)
    for r in data_rows:
        if len("\n".join(cur + [r])) > limit and len(cur) > len(header_rows):
            chunks.append("\n".join(cur))
            cur = list(header_rows) + [r]
        else:
            cur.append(r)
    chunks.append("\n".join(cur))
    return chunks


def _split_list_block(block: str, limit: int) -> List[str]:
    """列表超限：按列表项切分（保持每项的原子性）"""
    lines = block.splitlines()
    item_starts = [idx for idx, l in enumerate(lines) if _LIST_ITEM_RE.match(l)]
    if len(item_starts) <= 1:
        return [block]
    chunks: List[str] = []
    for j, start in enumerate(item_starts):
        end = item_starts[j + 1] if j + 1 < len(item_starts) else len(lines)
        chunks.append("\n".join(lines[start:end]))
    # 多行项内继行处理：简单起见，item_starts 间的内容已归入其所属项
    # 重新打包（项与项之间允许分到不同段，但单一项保持完整）
    return [c for c in chunks if c.strip()]


def _split_text_by_sentences(segment: str, limit: int) -> List[str]:
    """长文本按句子边界硬拆（用于段落超限），句子仍超限则按字符硬切（带尾部续接）"""
    sentences = [s for s in _SENTENCE_RE.split(segment) if s.strip()]
    if len(sentences) <= 1 and len(segment) <= limit:
        return [segment]
    chunks: List[str] = []
    cur = ""
    for s in sentences:
        if not cur and len(s) > limit:
            # 单句超限：按字符硬切，尾部成为下一个 chunk（续接）
            chunks.append(s[:limit])
            rest = s[limit:]
            while rest:
                if len(rest) <= limit:
                    chunks.append(rest)
                    rest = ""
                else:
                    chunks.append(rest[:limit])
                    rest = rest[limit:]
            continue
        if len(cur) + len(s) + 1 > limit and cur:
            chunks.append(cur.rstrip())
            cur = s
        else:
            cur = (cur + " " + s).strip() if cur else s
    if cur.strip():
        chunks.append(cur.rstrip())
    return [c for c in chunks if c.strip()]


def split_oversized_block(block: str, limit: int) -> List[str]:
    """把单个超限块按安全边界拆分（表格>行、列表>项、其余>句/行）"""
    if len(block) <= limit:
        return [block]
    if _is_table_block(block):
        return _split_table_block(block, limit)
    if _LIST_ITEM_RE.match(block.strip()):
        # 仅当块内确实含多个列表项时按项切，否则按句/行切
        parts = _split_list_block(block, limit)
        if len(parts) > 1:
            # 再把仍超限的单项继续按行/句拆
            out: List[str] = []
            for p in parts:
                out.extend(split_oversized_block(p, limit))
            return out
    # 其余：按句切，句仍超限则按行切，再超限则字符硬切
    return _split_text_by_sentences(block, limit)


def make_segments(blocks: List[str], limit: int) -> List[str]:
    """
    把块贪心打包成 ≤limit 的分段（每段为连续字符串）。

    规则（用户方案落地）：
    - 块完整放下：不拆
    - 块放不下：整块进入下一段（绝不在块中间切）
    - 块本身超限：按安全边界拆出子块；未消费的尾部成为下一段的开头（续接），
      若续接仍超限则继续顺延到更靠后的段（"第二次上传再处理"）

    Args:
        blocks: split_markdown_blocks 的输出
        limit: 每段字符上限

    Returns:
        list[str]：分段列表（每段 ≤ limit，除单一原子块确实无法再缩小的情形）
    """
    segments: List[str] = []
    cur = ""
    for b in blocks:
        if len(b) <= limit:
            # 原子块
            if cur and len(cur) + len(b) + 1 > limit:
                segments.append(cur.rstrip())
                cur = b
            elif cur:
                cur = cur + "\n\n" + b
            else:
                cur = b
            continue

        # 超限块：安全拆分后按同样的打包规则注入（尾部自动续接到下一段）
        for chunk in split_oversized_block(b, limit):
            if cur and len(cur) + len(chunk) + 1 > limit:
                segments.append(cur.rstrip())
                cur = chunk
            elif cur:
                cur = cur + "\n\n" + chunk
            else:
                cur = chunk
    if cur.strip():
        segments.append(cur.rstrip())
    return segments


def truncate_to_complete_blocks(text: str, limit: int) -> Tuple[str, str]:
    """
    在块边界处截断：返回 (完整块前缀, 剩余部分)。

    用于"总字数超了"时的防御性截断：前缀不切破任何块；剩余块原样返回，
    调用方可将剩余部分顺延到后续批次（"放到第二次上传再处理"）。
    """
    if not text:
        return "", ""
    if len(text) <= limit:
        return text, ""
    blocks = split_markdown_blocks(text)
    prefix_blocks: List[str] = []
    total = 0
    for b in blocks:
        if total + len(b) > limit and prefix_blocks:
            break
        prefix_blocks.append(b)
        total += len(b) + 2
    prefix = "\n\n".join(prefix_blocks)
    rest = text[len(prefix):].lstrip("\n")
    return prefix, rest
