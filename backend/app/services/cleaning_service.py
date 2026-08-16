"""
笔记清洗服务模块

本模块提供笔记内容的规则化清洗和文本分块功能，是 AI 清洗管道的核心组件。
清洗管道的目标是生成一份干净、无冗余的笔记副本，绝对不改变原意、不总结、不润色。

主要职责：
- 规则化清洗：去除空行、页眉页脚、去除水印等
- 文本分块：将长文本按段落边界分割为重叠块，用于后续嵌入和去重
- 清洗副本生成：根据去重结果标记重复块，生成清洗后的 Markdown

设计决策：
- 清洗规则仅做"减法"（去除冗余），不做"加法"（不添加内容）
- 重复块用 HTML 注释标记而非删除，用户可随时恢复
- 分块时保留重叠区域，避免语义在边界处断裂
- 所有清洗操作记录统计信息，便于用户审阅
"""

import difflib
import re
from typing import Dict, List, Optional, Tuple

from ..config import get_settings

settings = get_settings()


# ============================================================
# 规则化清洗
# ============================================================

# 常见页眉页脚模式（可扩展）
_HEADER_FOOTER_PATTERNS = [
    re.compile(r"^第\s*\d+\s*页\s*$", re.MULTILINE),           # "第 1 页"
    re.compile(r"^Page\s*\d+\s*$", re.MULTILINE | re.IGNORECASE),  # "Page 1"
    re.compile(r"^\s*\d+\s*$", re.MULTILINE),                   # 独立数字行（页码）
    re.compile(r"^<!--\s*page\s*=\s*\d+\s*-->\s*$", re.MULTILINE),  # MinerU 页码标签
]

# 水印关键词（可配置扩展）
# _WATERMARK_KEYWORDS = [
#     "水印", "watermark", "样本", "SAMPLE", "草稿", "DRAFT",
#     "仅供内部使用", "内部资料", "CONFIDENTIAL",
# ]
_WATERMARK_KEYWORDS = ["仅供内部使用", "内部资料", "CONFIDENTIAL", ]

# 全角标点 → 半角标点映射（暂不启用：可能破坏 Markdown 中的数学公式）
# 保留定义以便后续需要时启用
# _FULLWIDTH_TO_HALFWIDTH = {
#     "\u3000": " ",   # 全角空格 → 半角空格
#     "，": "，",       # 中文逗号保留
#     "。": "。",       # 中文句号保留
#     "；": "；",       # 中文分号保留
#     "：": "：",       # 中文冒号保留
#     "！": "！",       # 中文感叹号保留
#     "？": "？",       # 中文问号保留
#     "（": "（",       # 中文括号保留
#     "）": "）",       # 中文括号保留
# }

# OCR 常见错字修正表（高频且置信度极高的修正）
_OCR_CORRECTIONS = {
    "l0": "10", "O0": "00", "rn": "m",  # 常见 OCR 混淆
}


def clean_rules(text: str) -> Tuple[str, Dict[str, int]]:
    """
    对 Markdown 文本进行规则化清洗

    清洗规则（仅做减法，不改变原意）：
    1. 去除多余空行（连续 2+ 空行压缩为 1 个空行）
    2. 去除页眉页脚模式（页码、重复页眉等）
    3. 去除水印文字（可配置的水印关键词列表）
    4. 去除行首行尾多余空白

    Args:
        text: 原始 Markdown 文本

    Returns:
        Tuple[str, Dict[str, int]]: (清洗后的文本, 清洗统计信息)
        统计信息包含各规则的应用次数
    """
    stats = {
        "empty_lines_removed": 0,
        "headers_footers_removed": 0,
        "watermarks_removed": 0,
        "whitespace_trimmed": 0,
    }

    if not text or not text.strip():
        return text, stats

    # F-30 修复：逐行清洗并跟踪代码块/数学块状态。
    # 旧实现直接对全文做正则 sub，页码/数字行/水印规则会误删
    # 围栏代码块内的纯数字行（编号示例 1/2/3）、公式独立数字行等合法正文。
    lines = text.split("\n")
    result_lines = []
    in_code_block = False
    in_math_block = False

    for line in lines:
        stripped = line.strip()

        # 代码块 fence 切换（```）
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            result_lines.append(line)
            continue

        # 数学块切换（$$ 独占行；单行 $$...$$ 不切换状态）
        if stripped.startswith("$$"):
            if stripped.endswith("$$") and len(stripped) > 2:
                result_lines.append(line)
                continue
            in_math_block = not in_math_block
            result_lines.append(line)
            continue

        # 代码块/数学块内的行：不应用页眉页脚与水印规则，原样保留
        if in_code_block or in_math_block:
            result_lines.append(line)
            continue

        # 1. 页眉页脚模式（仅普通文本行）
        is_header_footer = False
        for pattern in _HEADER_FOOTER_PATTERNS:
            if pattern.match(stripped):
                is_header_footer = True
                stats["headers_footers_removed"] += 1
                break
        if is_header_footer:
            continue

        # 2. 水印文字（仅普通文本行，且为短行避免误删正文）
        is_watermark = False
        for keyword in _WATERMARK_KEYWORDS:
            if keyword in line and len(stripped) < 30:
                is_watermark = True
                stats["watermarks_removed"] += 1
                break
        if is_watermark:
            continue

        result_lines.append(line)

    result = "\n".join(result_lines)

    # 3. 去除多余空行（连续 2+ 空行压缩为 1 个空行）
    multi_blank_pattern = re.compile(r"\n{3,}")
    multi_blank_matches = multi_blank_pattern.findall(result)
    stats["empty_lines_removed"] = len(multi_blank_matches)
    result = multi_blank_pattern.sub("\n\n", result)

    # 4. 统一标点（暂不启用：可能破坏 Markdown 中的数学公式）
    # for fullwidth, halfwidth in _FULLWIDTH_TO_HALFWIDTH.items():
    #     result = result.replace(fullwidth, halfwidth)

    # 5. 去除行首行尾多余空白（保留缩进，仅去除纯空白行）
    lines = result.split("\n")
    trimmed_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            # 空行保留
            trimmed_lines.append("")
        elif stripped == line.rstrip() and line == line.lstrip():
            # 行首行尾无多余空白
            trimmed_lines.append(line)
        else:
            # 保留缩进（行首空格/tab），仅去除行尾空白
            trimmed = line.rstrip()
            if trimmed != line:
                stats["whitespace_trimmed"] += 1
            trimmed_lines.append(trimmed)
    result = "\n".join(trimmed_lines)

    # 6. 去除文件末尾多余空行
    result = result.rstrip("\n") + "\n"

    return result, stats


def split_into_chunks(
    text: str,
    chunk_size: int = 0,
    overlap: int = 0,
) -> List[Dict]:
    """
    将 Markdown 文本按段落边界分割为重叠块

    分块策略：
    1. 识别标题层级，按标题边界优先拆分
    2. 按段落（双换行符）将文本拆分为段落单元
    3. 将段落合并为块，直到接近 chunk_size（按字符数计算）
    4. 避免在代码块、列表项、续行处拆分
    5. 相邻块之间有 overlap 大小的重叠，避免语义断裂
    6. 超长标题下的子块重复标题前缀
    7. 记录每个块的标题层级路径（heading_context）

    Args:
        text: Markdown 文本
        chunk_size: 每个块的目标字符数，0 表示使用配置默认值
        overlap: 相邻块的重叠字符数，0 表示使用配置默认值

    Returns:
        List[Dict]: 分块列表，每个块包含：
            - index: 块序号（从 0 开始）
            - content: 块文本内容
            - start_line: 在原文中的起始行号（从 0 开始）
            - end_line: 在原文中的结束行号
            - char_count: 字符数
            - heading_context: 标题层级路径（如 "一级 > 二级 > 三级"）
    """
    if not text or not text.strip():
        return []

    chunk_size = chunk_size or settings.chunk_size
    overlap = overlap or settings.chunk_overlap

    # ============================================================
    # 步骤1：解析标题位置，构建标题层级
    # ============================================================
    heading_pattern = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)
    lines = text.split("\n")

    # 计算每行所属的标题路径
    line_heading_context: List[str] = [""] * len(lines)
    heading_stack: List[Tuple[int, str]] = []  # [(level, title)]
    in_code_block = False

    for line_idx, line in enumerate(lines):
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
        if in_code_block:
            if heading_stack:
                line_heading_context[line_idx] = " > ".join(h[1] for h in heading_stack)
            continue
        match = heading_pattern.match(line)
        if match:
            level = len(match.group(1))
            title = match.group(2).strip()
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
        if heading_stack:
            line_heading_context[line_idx] = " > ".join(h[1] for h in heading_stack)

    # ============================================================
    # 步骤2：按段落分割，考虑标题边界
    # ============================================================
    paragraphs = re.split(r"\n\n+", text)
    paragraphs = [p for p in paragraphs if p.strip()]

    if not paragraphs:
        return []

    # 计算每个段落在原文中的行号范围和标题上下文
    para_line_ranges = []
    para_heading_contexts = []
    line_idx = 0
    for para in paragraphs:
        para_lines = para.split("\n")
        start_line = line_idx
        end_line = line_idx + len(para_lines) - 1
        para_line_ranges.append((start_line, end_line))
        # 取段落首行的标题上下文
        if start_line < len(line_heading_context):
            para_heading_contexts.append(line_heading_context[start_line])
        else:
            para_heading_contexts.append("")
        line_idx = end_line + 1
        while line_idx < len(lines) and not lines[line_idx].strip():
            line_idx += 1

    # ============================================================
    # 步骤3：判断是否在特殊上下文中（代码块/列表/续行）
    # ============================================================
    def _is_in_special_context(para_text: str, all_lines: List[str], start: int, end: int) -> bool:
        """判断段落是否处于特殊上下文，不应作为拆分点"""
        # 检查是否在代码块内
        code_block_active = False
        for li in range(start, min(end + 1, len(all_lines))):
            if all_lines[li].strip().startswith("```"):
                code_block_active = not code_block_active
            if code_block_active:
                return True

        # 检查段落是否是列表项
        first_line_stripped = para_text.lstrip()
        if re.match(r"^[-*]\s+", first_line_stripped):
            return True
        if re.match(r"^\d+[.)]\s+", first_line_stripped):
            return True

        # 检查段落前一行是否以续行符号结尾（中英文冒号）
        if start > 0 and start - 1 < len(all_lines):
            prev_line = all_lines[start - 1].rstrip()
            if prev_line and prev_line[-1] in ("：", ":"):
                return True

        return False

    # ============================================================
    # 步骤4：将段落合并为块，考虑标题边界和特殊上下文
    # ============================================================
    chunks: List[Dict] = []
    current_chunk_parts: List[str] = []
    current_size = 0
    chunk_start_para_idx = 0
    chunk_heading_context = ""

    for i, para in enumerate(paragraphs):
        para_len = len(para)
        para_heading = para_heading_contexts[i]

        # 判断是否应该在此段落前拆分
        should_split = False
        if current_size + para_len > chunk_size and current_chunk_parts:
            should_split = True
            # 如果当前段落处于特殊上下文，推迟拆分
            if _is_in_special_context(para, lines, para_line_ranges[i][0], para_line_ranges[i][1]):
                # 仅当加上此段后不会超过 2 倍 chunk_size 时推迟
                if current_size + para_len <= chunk_size * 2:
                    should_split = False

        # 如果遇到新标题且当前块非空，优先拆分（标题边界）
        if current_chunk_parts and para_heading != chunk_heading_context:
            match = heading_pattern.match(para.split("\n")[0] if para.split("\n") else "")
            if match and current_size > 0:
                should_split = True

        if should_split:
            chunk_content = "\n\n".join(current_chunk_parts)
            start_line = para_line_ranges[chunk_start_para_idx][0]
            end_line = para_line_ranges[i - 1][1]

            chunks.append({
                "index": len(chunks),
                "content": chunk_content,
                "start_line": start_line,
                "end_line": end_line,
                "char_count": len(chunk_content),
                "heading_context": chunk_heading_context,
            })

            # 计算重叠：保留最后几个段落作为下一个块的开头
            overlap_parts = []
            overlap_size = 0
            for j in range(len(current_chunk_parts) - 1, -1, -1):
                part = current_chunk_parts[j]
                if overlap_size + len(part) > overlap:
                    break
                overlap_parts.insert(0, part)
                overlap_size += len(part) + 2

            current_chunk_parts = overlap_parts
            current_size = overlap_size
            chunk_start_para_idx = i - len(overlap_parts)

        # 更新当前块的标题上下文
        if not current_chunk_parts:
            chunk_heading_context = para_heading
        current_chunk_parts.append(para)
        current_size += para_len + 2

    # 提交最后一个块
    if current_chunk_parts:
        chunk_content = "\n\n".join(current_chunk_parts)
        start_line = para_line_ranges[chunk_start_para_idx][0]
        end_line = para_line_ranges[len(paragraphs) - 1][1]

        chunks.append({
            "index": len(chunks),
            "content": chunk_content,
            "start_line": start_line,
            "end_line": end_line,
            "char_count": len(chunk_content),
            "heading_context": chunk_heading_context,
        })

    # ============================================================
    # 步骤5：对超长标题块进行子分块，重复标题前缀
    # ============================================================
    final_chunks: List[Dict] = []
    for chunk in chunks:
        if len(chunk["content"]) > chunk_size and chunk["heading_context"]:
            # 按 chunk_size 再次拆分，但为子块添加标题前缀
            heading_prefix = ""
            for h_line in chunk["content"].split("\n"):
                if heading_pattern.match(h_line):
                    heading_prefix = h_line + "\n\n"
                    break

            sub_paragraphs = re.split(r"\n\n+", chunk["content"])
            sub_parts: List[str] = []
            current_part = ""
            for sp in sub_paragraphs:
                if sp.strip():
                    if len(current_part) + len(sp) > chunk_size and current_part:
                        sub_parts.append(current_part)
                        current_part = sp + "\n\n"
                    else:
                        current_part += sp + "\n\n"
            if current_part.strip():
                sub_parts.append(current_part)

            if len(sub_parts) <= 1:
                final_chunks.append(chunk)
                continue

            for idx, part in enumerate(sub_parts):
                # 为子块添加标题前缀（如果存在且子块不以标题开头）
                if heading_prefix and idx > 0 and not heading_pattern.match(part.lstrip()):
                    part_content = heading_prefix + part
                else:
                    part_content = part
                final_chunks.append({
                    "index": len(final_chunks),
                    "content": part_content,
                    "start_line": chunk["start_line"],
                    "end_line": chunk["end_line"],
                    "char_count": len(part_content),
                    "heading_context": chunk["heading_context"],
                })
        else:
            chunk["index"] = len(final_chunks)
            final_chunks.append(chunk)

    # 重新编号
    for i, chunk in enumerate(final_chunks):
        chunk["index"] = i

    return final_chunks


def generate_clean_copy(
    original_text: str,
    cleaned_text: str,
    duplicate_blocks: List[Dict],
) -> Tuple[str, Dict[str, int]]:
    """
    根据去重结果生成清洗副本

    对于被标记为重复的块，用 HTML 注释包裹，用户可随时恢复。
    保留首个出现的重复块，其余标记为重复。

    注意：split_into_chunks 产生的 chunk 可能有行范围重叠（overlap 机制），
    因此不能按 chunk 遍历输出行（会导致重叠行重复输出），
    改为逐行遍历，每行只输出一次。

    Args:
        original_text: 原始 Markdown 文本
        cleaned_text: 经过规则清洗后的 Markdown 文本
        duplicate_blocks: 重复块列表，每个包含：
            - block_index: 块序号
            - duplicate_of: 首次出现的块序号
            - similarity: 相似度分数

    Returns:
        Tuple[str, Dict[str, int]]: (清洗副本文本, 清洗统计信息)
    """
    stats = {
        "duplicate_blocks_marked": 0,
        "total_blocks": 0,
    }

    if not cleaned_text:
        return cleaned_text, stats

    # 如果没有重复块，直接返回清洗后的文本
    if not duplicate_blocks:
        return cleaned_text, stats

    # 构建需要标记的块索引集合
    duplicate_indices = set()
    duplicate_map = {}
    for dup in duplicate_blocks:
        idx = dup["block_index"]
        duplicate_indices.add(idx)
        duplicate_map[idx] = dup
        stats["duplicate_blocks_marked"] += 1

    # 重新分块，获取行范围信息
    chunks = split_into_chunks(cleaned_text)
    stats["total_blocks"] = len(chunks)

    if not chunks:
        return cleaned_text, stats

    lines = cleaned_text.split("\n")

    # 构建"行 → 所属 chunk"映射
    # 如果一行同时被 duplicate 和 non-duplicate chunk 覆盖，
    # 优先视为 non-duplicate（保留首次出现，不标记为重复）
    line_in_non_dup: set[int] = set()
    line_to_dup_info: Dict[int, Dict] = {}  # line_idx -> dup_info

    for chunk in chunks:
        chunk_index = chunk["index"]
        for line_idx in range(chunk["start_line"], chunk["end_line"] + 1):
            if chunk_index in duplicate_indices:
                if line_idx not in line_to_dup_info:
                    line_to_dup_info[line_idx] = duplicate_map[chunk_index]
            else:
                line_in_non_dup.add(line_idx)

    # 仅当一行只被 duplicate chunk 覆盖时，才标记为 duplicate
    duplicate_lines: set[int] = set()
    for line_idx in line_to_dup_info:
        if line_idx not in line_in_non_dup:
            duplicate_lines.add(line_idx)

    # 逐行输出，每行只输出一次
    result_lines = []
    in_duplicate = False

    for line_idx in range(len(lines)):
        is_dup = line_idx in duplicate_lines

        if is_dup and not in_duplicate:
            dup_info = line_to_dup_info[line_idx]
            # F-11 修复：注释标记使用重复块自身的 block_index（与前端传参、
            # metadata duplicates_detail 的 block_index 一致），
            # 旧实现误用 duplicate_of（保留块 index）导致恢复/删除无法匹配
            result_lines.append(
                f"<!-- duplicate: block_{dup_info['block_index']} "
                f"similarity={dup_info['similarity']:.2f} -->"
            )
            in_duplicate = True

        if not is_dup and in_duplicate:
            result_lines.append("<!-- /duplicate -->")
            in_duplicate = False

        result_lines.append(lines[line_idx])

    if in_duplicate:
        result_lines.append("<!-- /duplicate -->")

    return "\n".join(result_lines), stats


def restore_block(clean_text: str, block_index: int) -> str:
    """
    恢复被标记为重复的块

    移除指定块的 duplicate 注释标记，使其内容正常显示。

    Args:
        clean_text: 清洗后的 Markdown 文本
        block_index: 要恢复的块序号

    Returns:
        str: 恢复后的 Markdown 文本
    """
    # 查找匹配的 duplicate 注释块
    pattern = re.compile(
        rf"<!-- duplicate: block_{block_index} similarity=[\d.]+ -->\n(.*?)\n<!-- /duplicate -->",
        re.DOTALL,
    )
    # 移除注释标记，保留内容
    result = pattern.sub(r"\1", clean_text)
    return result


def delete_block(clean_text: str, block_index: int) -> str:
    """
    彻底删除被标记为重复的块

    连同内容和注释标记一起删除。

    Args:
        clean_text: 清洗后的 Markdown 文本
        block_index: 要删除的块序号

    Returns:
        str: 删除后的 Markdown 文本
    """
    # 查找匹配的 duplicate 注释块并整体删除
    pattern = re.compile(
        rf"<!-- duplicate: block_{block_index} similarity=[\d.]+ -->\n.*?\n<!-- /duplicate -->\n*",
        re.DOTALL,
    )
    result = pattern.sub("", clean_text)
    return result


# ============================================================
# 无模型兜底去重（嵌入模型不可用时的降级方案）
# ============================================================

def _normalize_for_dedup(text: str) -> str:
    """归一化文本用于去重比较：压缩连续空白并去除首尾空白、忽略大小写"""
    return " ".join(text.split()).lower()


def _text_similarity(a: str, b: str, threshold: float) -> float:
    """
    基于标准库的文本相似度

    精确相等返回 1.0；否则用 difflib.SequenceMatcher 计算相似度。
    先用 quick_ratio（ratio 的快速上界，O(n) 代价）做预过滤，
    只有可能达到阈值的块对才执行精确的 ratio 计算，控制最坏情况开销。

    Args:
        a: 归一化后的文本 A
        b: 归一化后的文本 B
        threshold: 相似度阈值

    Returns:
        float: 相似度，低于阈值直接返回 0.0（避免无谓计算）
    """
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    # autojunk=False：默认的 autojunk 会把出现频率高的字符（中文高频字如"的/了/。"）
    # 当作垃圾丢弃，导致中文文本相似度被严重低估（实测 2% 差异的文本 ratio 仅 0.5）
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    # quick_ratio 是 ratio 的快速上界：上界都达不到阈值，真实值必然低于阈值
    if matcher.quick_ratio() < threshold:
        return 0.0
    return matcher.ratio()


def find_duplicates_lightweight(
    chunks: List[Dict],
    threshold: float = 0.0,
) -> List[Dict]:
    """
    无模型兜底去重：基于归一化文本相似度查找重复块

    嵌入模型不可用（如内存不足导致加载失败）时的降级方案，仅使用标准库，
    内存占用可忽略。语义与 VectorStore.find_duplicates 保持一致：
    - 保留首次出现的块，后续相似块标记为重复
    - 跳过行范围有重叠的块对（重叠是分块策略的正常结果，非真正重复）
    - 按相似度降序排列

    Args:
        chunks: 分块列表，每个包含 index、content、start_line、end_line
        threshold: 相似度阈值，0 表示使用配置默认值

    Returns:
        List[Dict]: 重复块列表，每个包含：
            - block_index: 重复块的索引
            - duplicate_of: 首次出现的块索引
            - similarity: 相似度分数
    """
    threshold = threshold or settings.similarity_threshold

    n = len(chunks)
    if n < 2:
        return []

    normalized = [_normalize_for_dedup(chunk["content"]) for chunk in chunks]

    duplicates = []
    already_duplicate = set()  # 已被标记为重复的块索引

    for i in range(n):
        if i in already_duplicate:
            continue  # 已被标记为重复的块不再作为"首次出现"

        for j in range(i + 1, n):
            if j in already_duplicate:
                continue  # 已被标记为重复的块跳过

            # 跳过行范围有重叠的块对（重叠是分块策略的正常结果，非真正重复）
            i_start = chunks[i].get("start_line", 0)
            i_end = chunks[i].get("end_line", 0)
            j_start = chunks[j].get("start_line", 0)
            j_end = chunks[j].get("end_line", 0)
            if i_start <= j_end and j_start <= i_end:
                continue

            similarity = _text_similarity(normalized[i], normalized[j], threshold)
            if similarity >= threshold:
                # j 是 i 的重复块
                duplicates.append({
                    "block_index": chunks[j]["index"],
                    "duplicate_of": chunks[i]["index"],
                    "similarity": similarity,
                })
                already_duplicate.add(j)

    # 按相似度降序排列
    duplicates.sort(key=lambda x: x["similarity"], reverse=True)
    return duplicates
