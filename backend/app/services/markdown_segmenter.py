# -*- coding: utf-8 -*-
"""
Markdown 结构感知分段器（按结构块边界切分，避免截断，见 docs/decisions.md#F-34）
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
from dataclasses import dataclass
from typing import List, Optional, Tuple

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


@dataclass(frozen=True)
class Segment:
    """带定位信息的分段（阶段 2.7：引用可回跳）

    ## 为什么需要定位信息

    检索返回的引用如果只有一段文本，用户看到的是"这句话来自某篇笔记"，
    却**无法回到原文核对**。产品承诺是"可追溯"，而"可追溯"的最低要求是
    能把用户送到原文的那个位置。这就必须有字符偏移。

    ## 核心不变量

        text[char_start:char_end] == content

    这条不变量是整个回跳功能的基石：前端拿到 `char_start/char_end`
    去原文里 `slice` 并高亮，**切出来的必须就是检索到的那段**。
    偏移错一位，高亮的就不是答案，用户会以为系统在胡说。
    因此它有专门的属性测试（`tests/test_markdown_segmenter.py`：
    `TestOffsetInvariant` / `TestOffsetInvariantOnRealCorpus`），
    既有手写样例也有**全部真实 markdown** 的穷举断言 —— 这条不变量
    在开发过程中被违反过两次（各 1318 个分段），两次都是靠穷举才发现的。

    Attributes:
        content: 分段文本
        char_start: 在**原始全文**中的起始字符下标（含）
        char_end: 在原始全文中的结束字符下标（不含）
        heading_path: 该分段起始位置的标题层级路径（如 "第一章 > 1.2 保护配置"）
        block_index: 该分段首块在 `split_markdown_blocks` 结果中的下标
        line_start: 起始行号（0 基，含）
        line_end: 结束行号（0 基，含）

    行号为什么也要留着：检索 chunk 的历史消费方按**行**定位 ——
    Chroma 元数据存 `start_line/end_line`，清洗结果里的重复块恢复/删除
    也按行区间操作（`restore_block` / `delete_block`）。
    字符偏移是新增能力，行号是既有契约，两者都要给。
    """
    content: str
    char_start: int
    char_end: int
    heading_path: str = ""
    block_index: int = 0
    line_start: int = 0
    line_end: int = 0

    def as_dict(self) -> dict:
        return {
            "content": self.content,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "heading_path": self.heading_path,
            "block_index": self.block_index,
            "line_start": self.line_start,
            "line_end": self.line_end,
        }


def split_markdown_blocks(text: str) -> List[str]:
    """把 Markdown 文本切成结构完整的块（原子单元），返回块列表（不含空块）"""
    return [b for b, _ in _split_blocks_with_offsets(text)]


def _split_blocks_with_offsets(text: str) -> List[Tuple[str, int]]:
    """把 Markdown 切成结构块，并**在切块时**记录每块在原文中的起始下标

    返回 [(块文本, 起始下标), ...]（块文本已 strip 首尾换行，下标指向 strip 后的首字符）

    ## 为什么必须在切块时记录，而不是事后 `text.find()` 搜索

    第一版让 `split_markdown_blocks` 照旧返回文本，再用 `text.find(block, cursor)`
    逐个定位。这在真实语料上**大量出错**：实测 61 个真实 markdown、
    10926 个分段中有 **1318 个违反** `text[char_start:char_end] == content`
    （12%），且偏差可以很大（首个分段的 `char_start=0`，
    却切出了文件后半部分的内容）。

    失效机制：块文本被 `.strip("\\n")` 过，`find` 可能失败；一旦失败就
    回退找首行，而 `cursor` 仍按**整块长度**推进 —— 此后每个块的搜索起点
    都偏了，错误会**沿文档累积**。这类"偏移整体漂移"的错误在单文件抽查中
    很容易漏过（第一版只测了 1 个文件，恰好没触发）。

    改为在扫描时直接记录 `line_start_offset[i]`，位置由构造保证正确，
    不存在"搜不到"或"搜到别处"的可能。
    """
    if not text:
        return []

    lines = text.split("\n")
    n = len(lines)
    #: 每行首字符在原文中的下标
    line_start: List[int] = []
    _off = 0
    for line in lines:
        line_start.append(_off)
        _off += len(line) + 1  # +1 为 "\n"

    blocks: List[Tuple[str, int]] = []
    i = 0

    def push(buf: List[str], first_line_idx: int) -> None:
        s = "\n".join(buf).strip("\n")
        if not s.strip():
            return
        # strip 掉的前导换行要补回偏移，保证从 s 的首字符开始
        offset = line_start[first_line_idx]
        # 该块的首行若前导换行被 strip（块以空行开头的情况），按实际首字符修正
        leading = len("\n".join(buf)) - len("\n".join(buf).lstrip("\n"))
        blocks.append((s, offset + leading))

    while i < n:
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue

        block_first = i

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
            push(buf, block_first)
            continue

        # 标题：单行即块（标题本身是原子）
        if HEADING_RE.match(stripped):
            push([lines[i]], block_first)
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
            push(buf, block_first)
            continue

        # 分隔线
        if HR_RE.match(stripped):
            push([lines[i]], block_first)
            i += 1
            continue

        # 表格：连续以 | 开头的行（含表头/分隔行）为一个块
        if TABLE_ROW_RE.match(stripped):
            buf = [lines[i]]
            i += 1
            while i < n and TABLE_ROW_RE.match(lines[i].strip()):
                buf.append(lines[i])
                i += 1
            push(buf, block_first)
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
            push(buf, block_first)
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
        push(buf, block_first)

    return blocks


def _is_table_block(block: str) -> bool:
    lines = [line for line in block.splitlines() if line.strip()]
    return bool(lines) and all(TABLE_ROW_RE.match(line.strip()) for line in lines)


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
    item_starts = [idx for idx, line in enumerate(lines) if _LIST_ITEM_RE.match(line)]
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


def to_retrieval_chunks(segments: List[Segment]) -> List[dict]:
    """把 `Segment` 转成检索 chunk 的 dict 契约（`clean_tasks` 与 Chroma 元数据用）

    产出结构与已被取代的 `cleaning_service.split_into_chunks` **逐字段兼容**：

        {index, content, start_line, end_line, char_start, char_end,
         char_count, heading_context}

    保留旧字段名（`start_line`/`heading_context`）而不是改名，是因为它们已经
    流到了两个下游：Chroma 的向量元数据、以及前端按 `block_index` 恢复重复块
    的注释标记。改名会让历史数据与新数据对不上。新增的
    `char_start/char_end` 正是阶段 2.7 回跳所需。

    ## 为什么这里没有 overlap 参数

    第一版加过一个 `overlap` 参数，实现是"把 `char_start` 往前挪 n 个字符
    但内容不变" —— 那会**直接破坏核心不变量**
    （`text[char_start:char_end] == content`），前端按偏移高亮就会多选一段。

    重叠必须在**分段时**做（扩展区间、内容随之变长），而不是在转换层假装。
    正解是 `segment_with_offsets(text, limit, overlap_blocks=n)`。
    """
    return [
        {
            "index": i,
            "content": seg.content,
            "start_line": seg.line_start,
            "end_line": seg.line_end,
            "char_start": seg.char_start,
            "char_end": seg.char_end,
            "char_count": len(seg.content),
            "heading_context": seg.heading_path,
        }
        for i, seg in enumerate(segments)
    ]


# ---------------------------------------------------------------------------
# 带定位信息的分段（阶段 2.1 / 2.7）
# ---------------------------------------------------------------------------

def _heading_path_at(text: str, position: int) -> str:
    """求 `position` 处生效的标题层级路径（如 "第一章 > 1.2 保护配置"）

    只扫描 `position` **之前**的标题行，维护一个层级栈：
    遇到同级或更高级的标题就弹出，从而得到"当前所处位置"的完整路径。
    代码围栏内的 `#` 行不算标题（与 `split_markdown_blocks` 的处理保持一致）。
    """
    stack: List[Tuple[int, str]] = []
    in_fence = False
    fence_char = ""
    cursor = 0

    # 处理所有**起始位置早于 position** 的行。注意要用 "起始位置" 判断而不是
    # "下一行起始位置"：`cursor >= position` 的写法会把紧邻 position 的
    # 最后一行（常常正是该段所属的标题）跳过去，导致三级路径丢失。
    for line in text.split("\n"):
        if cursor > position:
            break
        stripped = line.strip()

        # 维护围栏状态：围栏内的 # 不是标题
        m = FENCE_RE.match(stripped)
        if m:
            ch = m.group(1)[0]
            if not in_fence:
                in_fence, fence_char = True, ch
            elif ch == fence_char:
                in_fence = False
        elif not in_fence:
            h = HEADING_RE.match(stripped)
            if h:
                level = len(h.group(1))
                title = h.group(2).strip()
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, title))

        cursor += len(line) + 1  # +1 为被 split 掉的 "\n"

    return " > ".join(t for _, t in stack)


def _line_index(text: str) -> List[int]:
    """每行首字符在原文中的下标（用于 字符偏移 → 行号 的换算）

    行号与偏移必须能互相换算：检索消费方（Chroma 元数据、重复块恢复）
    按行定位，而回跳高亮按偏移定位，两者描述的是同一个区间。
    """
    starts: List[int] = []
    off = 0
    for line in text.split("\n"):
        starts.append(off)
        off += len(line) + 1  # +1 为 "\n"
    return starts


def _line_of(line_starts: List[int], offset: int) -> int:
    """二分求 `offset` 落在第几行（0 基）"""
    import bisect

    # bisect_right - 1：找到最后一个首字符 ≤ offset 的行
    return max(0, bisect.bisect_right(line_starts, offset) - 1)


def _split_block_into_spans(
    text: str, start: int, end: int, limit: int,
) -> List[Tuple[int, int]]:
    """把超限块 `text[start:end]` 按安全边界拆成若干**字符区间**

    返回 [(seg_start, seg_end), ...]，各区间的切片**顺序拼接后恰好等于原文片段**
    （不丢字符、不加字符）。

    ## 为什么必须是"直接切片"而不是"先拆字符串再回头找"

    原实现是 `split_oversized_block(block, limit)` 返回子块**字符串**，
    再调用 `text.find(part, ...)` 反查位置。这在真实语料上**大量失败**：
    实测 61 个文件中 **10 个（16%）内容丢失 8%~16%**，
    最严重的一个 41962 字的文件丢了 3410 字 —— 而且完全静默。

    根因是子块文本被**改写**过：段落按句拆分时用 `" "` 重新拼接，
    而中文原文里句子之间**没有空格**（`"第0句。第1句。"` → `"第0句。 第1句。"`），
    于是 `find` 一律返回 -1，子块被整个丢弃。

    正确做法是不要"拆了再找"，而是**在原文上算边界、直接切片**：
    切出来的东西按定义就与原文一致，不存在"找不到"的可能。
    边界优先落在句末标点或换行处，与 `split_oversized_block` 的安全边界
    语义一致（不切破句子/行）。
    """
    if end <= start:
        return []
    if end - start <= limit:
        return [(start, end)]

    spans: List[Tuple[int, int]] = []
    seg_start = start
    i = start
    while i < end:
        if i - seg_start >= limit:
            # 从 i 往前找最近的句末标点或换行作为切点
            cut = i
            probe = i
            floor = seg_start + max(1, limit // 2)  # 避免切得过碎
            while probe > floor:
                ch = text[probe - 1]
                if ch in "。！？!?；;\n":
                    cut = probe
                    break
                probe -= 1
            if cut <= seg_start:
                cut = i  # 找不到安全边界 → 退化为硬切（与旧实现一致）
            spans.append((seg_start, cut))
            seg_start = cut
        i += 1

    if seg_start < end:
        spans.append((seg_start, end))
    return spans


def segment_with_offsets(
    text: str,
    limit: int = 1200,
    *,
    overlap_blocks: int = 0,
) -> List[Segment]:
    """把 Markdown 切成带定位信息的分段（阶段 2.1 的统一分块，2.7 的回跳基础）

    ## 与既有两套实现的关系

    本仓库此前有**两套**分块实现（§2.1 S-3）：

    | | `cleaning_service.split_into_chunks` | `markdown_segmenter` |
    |---|---|---|
    | 产出 | `{content, start_line, end_line, heading_context}` | 纯文本段 |
    | 用途 | 检索 chunk（→ 嵌入 → Chroma） | LLM 抽取的填充边界 |
    | 强项 | 行号、标题路径、overlap | 结构块原子性 |

    本函数把两侧强项合到一起：**结构块原子性 + 字符偏移 + 完整标题路径 +
    可选 overlap**。这就是 2.1 要的那个"唯一实现"。

    ## 为什么偏移是对的（核心不变量）

        text[char_start:char_end] == content

    实现方式是**按块拼接并累计偏移**，而不是事后在原文里搜内容 ——
    后者遇到重复段落时会定位到错误的位置。这里 offset 是从
    `_block_offsets` 得到的真实下标，`char_end = char_start + len(首块)`
    逐块推进；拼接时块之间用 `"\\n\\n"` 连接，**连接符也计入区间**，
    因此切片结果与 content 逐字相同。

    ## overlap 为什么不破坏不变量

    `overlap_blocks > 0` 时，每段会**向前多带**前一段末尾的若干块。
    偏移随之向前扩展（`char_start` 提前到那些块的位置），
    所以切片仍然逐字相等 —— 重叠只是让区间变长，不会让内容与区间错位。
    这也是刻意不用"字符级 overlap"的原因：那种做法要么破坏不变量，
    要么必须在区间外虚构内容。

    Args:
        text: Markdown 原文
        limit: 每段字符上限（软上限：单个原子块超限时会按安全边界拆）
        overlap_blocks: 与上一段重叠的块数（0 = 不重叠）

    Returns:
        List[Segment]
    """
    if not text or not text.strip():
        return []

    blocks_with_offsets = _split_blocks_with_offsets(text)
    if not blocks_with_offsets:
        return []
    blocks = [b for b, _ in blocks_with_offsets]
    offsets = [o for _, o in blocks_with_offsets]
    n_blocks = len(blocks)
    #: 每个块在原文中的结束下标（含）。用于把"块区间"翻译成"字符区间"。
    ends = [offsets[i] + len(blocks[i]) for i in range(n_blocks)]
    line_starts = _line_index(text)

    def make(seg_start: int, seg_end: int, block_idx: int) -> Segment:
        return Segment(
            content=text[seg_start:seg_end],
            char_start=seg_start,
            char_end=seg_end,
            heading_path=_heading_path_at(text, seg_start),
            block_index=block_idx,
            line_start=_line_of(line_starts, seg_start),
            # seg_end 是开区间 → 末字符在 seg_end-1
            line_end=_line_of(line_starts, max(seg_start, seg_end - 1)),
        )

    # 先按块边界打包成 [start_block_idx, end_block_idx) 区间
    ranges: List[Tuple[int, int]] = []
    sub_segments: List[Segment] = []
    cur_start: Optional[int] = None
    cur_len = 0

    for i, block in enumerate(blocks):
        if len(block) <= limit:
            if cur_start is None:
                cur_start, cur_len = i, len(block)
            elif cur_len + len(block) + 2 > limit:
                ranges.append((cur_start, i))
                cur_start, cur_len = i, len(block)
            else:
                cur_len += len(block) + 2
            continue

        # 超限块必须拆开：先把已累积的区间封口，再让每个子块各自成段。
        # **不能把子块与相邻块合并进同一个区间** —— 区间是按块下标表示的，
        # 合并后无法表达"这是块的中间一段"，切片就会错位。
        if cur_start is not None:
            ranges.append((cur_start, i))
            cur_start, cur_len = None, 0
        for sub_start, sub_end in _split_block_into_spans(text, offsets[i], ends[i], limit):
            sub_segments.append(make(sub_start, sub_end, i))
    if cur_start is not None:
        ranges.append((cur_start, n_blocks))

    segments: List[Segment] = []
    for range_idx, (b_start, b_end) in enumerate(ranges):
        # overlap：把上一段末尾的若干块并入本段开头
        if overlap_blocks > 0 and range_idx > 0:
            prev_start = ranges[range_idx - 1][0]
            b_start = max(prev_start, b_start - overlap_blocks)

        # **从原文直接切片**，而不是 `"\n\n".join(blocks[...])`。
        #
        # 第一版用 join 拼接，结果在真实语料上 12% 的分段违反了不变量：
        # 块与块在原文里可能只隔 1 个换行（实测 `<!-- page=1 -->\n# 标题`），
        # 而 join 一律插入 `"\n\n"` —— 内容比原文切片多 1 个字符，
        # 于是 `char_end = char_start + len(content)` 整体偏大，
        # 前端按偏移高亮就会**多选中一个字符并逐段累积偏移**。
        #
        # 直接切片后，`text[char_start:char_end] == content` 由构造保证
        # （content 就是切片本身），不再依赖"join 的分隔符恰好等于原文"。
        # 代价是分段内容可能含块间的原始换行，与旧 `split_into_chunks`
        # 的 `"\n\n"` 拼接略有差异 —— 这不影响检索（多一个换行不改变语义），
        # 但换来的是定位绝对正确。
        start_off = offsets[b_start]
        end_off = ends[b_end - 1]
        seg = make(start_off, end_off, b_start)
        if not seg.content.strip():
            continue
        segments.append(seg)

    # 超限块的子段按位置插回，保持文档顺序
    if sub_segments:
        merged = segments + sub_segments
        merged.sort(key=lambda s: (s.char_start, s.char_end))
        return merged
    return segments
