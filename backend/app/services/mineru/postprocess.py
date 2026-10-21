"""
Markdown 后处理模块

对转换后的 Markdown 内容进行清洗，包括：
- 统一换行符
- 合并多余空行
- 过滤水印文本
- 还原「整行粗体」（MinerU 4.x 的排版习惯，见 `_unwrap_whole_line_bold`）
"""

from __future__ import annotations

import re
from typing import Dict, Any

#: 整行就是一个粗体块：`**正文**`（首尾都是 `**`，中间不含 `**`）
#:
#: 用 `.+?` + 前后 `(?=\S)`/`(?<=\S)` 而不是 `.*`：避免把 `****` 这种空块
#: 或首尾带空格的残缺标记也算进来。
_WHOLE_LINE_BOLD = re.compile(r"^(\s*)\*\*(?=\S)(.+?)(?<=\S)\*\*\s*$")


def clean_markdown(content: str, config: Dict[str, Any]) -> str:
    """
    清洗 Markdown 内容

    依次执行：统一换行符 → 还原整行粗体 → 合并空行 → 过滤水印

    Args:
        content: 原始 Markdown 内容
        config: 配置字典，包含 max_blank_lines 和 watermark_patterns

    Returns:
        清洗后的 Markdown 内容
    """
    if not content:
        return content

    content = _unify_newlines(content)
    content = _unwrap_whole_line_bold(content)
    content = _merge_blank_lines(content, config.get("max_blank_lines", 2))
    content = _filter_watermarks(content, config)
    return content


def _unwrap_whole_line_bold(content: str) -> str:
    """
    把「整行就是一个粗体块」还原成普通段落。

    ## 为什么需要它（2026-10-21，MinerU 4.x 迁移）

    MinerU 4.x 的 `basic` / `standard` 档会把**绝大多数正文段落整行包成
    `**…**`**。实测一份 2 页的技术标准 PDF（走查时上传的样本）：

        非空行 51 行，其中 45 行是整行粗体（88%）

    前端按 Markdown 渲染出来就是**满屏粗体**，阅读体验明显劣化；
    对照之下云端解析（`vlm-http-client`）的产物是干净的普通段落。

    ## 边界

    只处理**整行**都是粗体的情况；行内粗体（真正的强调，如
    `这里**必须**确认`）一律不动。判定方式是"去掉首尾 `**` 后中间不再含 `**`"，
    因此 `**a** 与 **b**` 这种一行里两个粗体块**不会**被误还原。
    """
    out = []
    for line in content.split("\n"):
        match = _WHOLE_LINE_BOLD.match(line)
        if match and "**" not in match.group(2):
            out.append(f"{match.group(1)}{match.group(2)}")
        else:
            out.append(line)
    return "\n".join(out)


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
