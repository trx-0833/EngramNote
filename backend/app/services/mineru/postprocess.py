"""
Markdown 后处理模块

对转换后的 Markdown 内容进行清洗，包括：
- 统一换行符
- 合并多余空行
- 过滤水印文本
"""

from __future__ import annotations

import re
from typing import Dict, Any


def clean_markdown(content: str, config: Dict[str, Any]) -> str:
    """
    清洗 Markdown 内容

    依次执行：统一换行符 → 合并空行 → 过滤水印

    Args:
        content: 原始 Markdown 内容
        config: 配置字典，包含 max_blank_lines 和 watermark_patterns

    Returns:
        清洗后的 Markdown 内容
    """
    if not content:
        return content

    content = _unify_newlines(content)
    content = _merge_blank_lines(content, config.get("max_blank_lines", 2))
    content = _filter_watermarks(content, config)
    return content


def _unify_newlines(content: str) -> str:
    """统一换行符为 LF"""
    return content.replace("\r\n", "\n").replace("\r", "\n")


def _merge_blank_lines(content: str, max_blank: int = 2) -> str:
    """将连续超过 max_blank 的空行合并为 max_blank 行"""
    target = "\n" * max_blank
    pattern = re.compile(r"\n{" + str(max_blank + 1) + r",}")
    return pattern.sub(target, content)


def _filter_watermarks(content: str, config: Dict[str, Any]) -> str:
    """
    过滤水印文本

    根据配置中的水印正则模式，统计每个文本行出现的次数，
    出现 3 次及以上的匹配行视为水印，从内容中移除。
    """
    patterns = config.get("watermark_patterns", [])
    if not patterns:
        return content

    compiled_patterns = []
    for p in patterns:
        try:
            compiled_patterns.append(re.compile(p, re.IGNORECASE))
        except re.error:
            pass

    lines = content.split("\n")
    line_counts: Dict[str, int] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        for cp in compiled_patterns:
            if cp.search(stripped):
                line_counts[stripped] = line_counts.get(stripped, 0) + 1
                break

    watermark_lines = {
        stripped for stripped, count in line_counts.items() if count >= 3
    }

    filtered = []
    for line in lines:
        stripped = line.strip()
        if stripped in watermark_lines:
            continue
        filtered.append(line)

    return "\n".join(filtered)
