"""
MinerU 数据模型定义

定义文档转换的核心数据结构，包括源类型枚举和转换结果数据类。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class SourceType(enum.Enum):
    """文档源类型枚举"""
    PDF = "pdf"
    IMAGE = "image"
    DOCX = "docx"
    PPTX = "pptx"
    XLSX = "xlsx"


@dataclass
class ConversionResult:
    """
    文档转换结果

    包含转换后的 Markdown 内容、元数据以及可能的错误信息。
    整个模块的返回值共识，所有转换函数均返回此数据类实例。
    """
    title: str = ""
    markdown_content: str = ""
    source_type: SourceType = SourceType.PDF
    source_path: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    success: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """将转换结果序列化为字典"""
        return {
            "title": self.title,
            "markdown_content": self.markdown_content,
            "source_type": self.source_type.value,
            "source_path": self.source_path,
            "metadata": self.metadata,
            "error": self.error,
            "success": self.success,
        }


# 支持的文件扩展名集合
SUPPORTED_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".docx", ".pptx", ".xlsx"}
PDF_EXTS = {".pdf"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg"}
DOCX_EXTS = {".docx"}
PPTX_EXTS = {".pptx"}
XLSX_EXTS = {".xlsx"}
