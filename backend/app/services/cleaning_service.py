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

    result = text

    # 1. 去除页眉页脚模式
    for pattern in _HEADER_FOOTER_PATTERNS:
        matches = pattern.findall(result)
        if matches:
            stats["headers_footers_removed"] += len(matches)
            result = pattern.sub("", result)

    # 2. 去除水印文字（整行包含水印关键词的行）
    lines = result.split("\n")
    cleaned_lines = []
    for line in lines:
        is_watermark = False
        for keyword in _WATERMARK_KEYWORDS:
            if keyword in line and len(line.strip()) < 30:
                # 仅对短行（小于30个字符）判定为水印，避免误删正文
                is_watermark = True
                break
        if is_watermark:
            stats["watermarks_removed"] += 1
        else:
            cleaned_lines.append(line)
    result = "\n".join(cleaned_lines)

    # 3. 去除多余空行（连续 2+ 空行压缩为 1 个空行）
    # 先统计有多少处连续空行
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

    print("规则化清洗完成！\n")

    return result, stats


def split_into_chunks(
    text: str,
    chunk_size: int = 0,
    overlap: int = 0,
) -> List[Dict]:
    """
    将 Markdown 文本按段落边界分割为重叠块

    分块策略：
    1. 按段落（双换行符）将文本拆分为段落单元
    2. 将段落合并为块，直到接近 chunk_size（按字符数计算）
    3. 相邻块之间有 overlap 大小的重叠，避免语义断裂
    4. 记录每个块在原文中的行号范围，便于后续定位

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
    """
    if not text or not text.strip():
        return []

    chunk_size = chunk_size or settings.chunk_size
    overlap = overlap or settings.chunk_overlap

    # 按段落分割
    paragraphs = re.split(r"\n\n+", text)
    # 过滤空段落
    paragraphs = [p for p in paragraphs if p.strip()]

    if not paragraphs:
        return []

    chunks = []
    current_chunk_parts = []
    current_size = 0
    chunk_start_para_idx = 0

    # 计算每个段落在原文中的行号范围
    lines = text.split("\n")
    para_line_ranges = []
    line_idx = 0
    for para in paragraphs:
        para_lines = para.split("\n")
        start_line = line_idx
        end_line = line_idx + len(para_lines) - 1
        para_line_ranges.append((start_line, end_line))
        line_idx = end_line + 1
        # 跳过段落间的空行
        while line_idx < len(lines) and not lines[line_idx].strip():
            line_idx += 1

    for i, para in enumerate(paragraphs):
        para_len = len(para)

        # 如果当前块 + 当前段落超过 chunk_size，且当前块非空，则提交当前块
        if current_size + para_len > chunk_size and current_chunk_parts:
            chunk_content = "\n\n".join(current_chunk_parts)
            start_line = para_line_ranges[chunk_start_para_idx][0]
            end_line = para_line_ranges[i - 1][1]

            chunks.append({
                "index": len(chunks),
                "content": chunk_content,
                "start_line": start_line,
                "end_line": end_line,
                "char_count": len(chunk_content),
            })

            # 计算重叠：保留最后几个段落作为下一个块的开头
            overlap_parts = []
            overlap_size = 0
            for j in range(len(current_chunk_parts) - 1, -1, -1):
                part = current_chunk_parts[j]
                if overlap_size + len(part) > overlap:
                    break
                overlap_parts.insert(0, part)
                overlap_size += len(part) + 2  # +2 for \n\n

            current_chunk_parts = overlap_parts
            current_size = overlap_size
            chunk_start_para_idx = i - len(overlap_parts)

        current_chunk_parts.append(para)
        current_size += para_len + 2  # +2 for \n\n separator

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
        })

    return chunks


def generate_clean_copy(
    original_text: str,
    cleaned_text: str,
    duplicate_blocks: List[Dict],
) -> Tuple[str, Dict[str, int]]:
    """
    根据去重结果生成清洗副本

    对于被标记为重复的块，用 HTML 注释包裹，用户可随时恢复。
    保留首个出现的重复块，其余标记为重复。

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

    # 重新分块，对重复块进行标记
    chunks = split_into_chunks(cleaned_text)
    stats["total_blocks"] = len(chunks)

    if not chunks:
        return cleaned_text, stats

    # 构建清洗副本：将重复块用 HTML 注释包裹
    lines = cleaned_text.split("\n")
    result_lines = []

    for chunk in chunks:
        start_line = chunk["start_line"]
        end_line = chunk["end_line"]
        chunk_index = chunk["index"]

        if chunk_index in duplicate_indices:
            dup_info = duplicate_map[chunk_index]
            # 用 HTML 注释标记重复块
            result_lines.append(f"<!-- duplicate: block_{dup_info['duplicate_of']} similarity={dup_info['similarity']:.2f} -->")
            for line_idx in range(start_line, end_line + 1):
                if line_idx < len(lines):
                    result_lines.append(lines[line_idx])
            result_lines.append("<!-- /duplicate -->")
        else:
            for line_idx in range(start_line, end_line + 1):
                if line_idx < len(lines):
                    result_lines.append(lines[line_idx])

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
