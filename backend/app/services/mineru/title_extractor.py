"""
标题提取模块

从文件路径和 Markdown 内容中提取文档标题。
优先级：PDF 元数据标题 > Markdown 首个标题 > 文件名
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


def extract_title(
    file_path: str | Path, markdown_content: str, source_type: str
) -> str:
    """
    提取文档标题

    按优先级依次尝试：
    1. PDF 文件的元数据标题（仅 PDF 类型）
    2. Markdown 内容中的首个一级标题
    3. 文件名（不含扩展名）

    Args:
        file_path: 文件路径
        markdown_content: Markdown 内容
        source_type: 源类型字符串

    Returns:
        提取到的标题
    """
    file_path = Path(file_path) if isinstance(file_path, str) else file_path

    if source_type == "pdf":
        metadata_title = _extract_pdf_metadata_title(file_path)
        if metadata_title:
            return metadata_title

    first_heading = _extract_first_heading(markdown_content)
    if first_heading:
        return first_heading

    return file_path.stem


def _extract_pdf_metadata_title(file_path: Path) -> Optional[str]:
    """从 PDF 元数据中提取标题"""
    try:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(str(file_path))
        try:
            metadata = doc.get_metadata_dict()
            title = metadata.get("Title", "")
            if title and title.strip():
                return title.strip()
        finally:
            doc.close()
    except Exception:
        pass
    return None


def _extract_first_heading(markdown_content: str) -> Optional[str]:
    """从 Markdown 内容中提取首个一级标题"""
    if not markdown_content:
        return None
    match = re.search(r"^#\s+(.+)$", markdown_content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None
