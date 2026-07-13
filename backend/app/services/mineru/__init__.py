"""
MinerU 文档转换子包

本子包提供文档解析和转换功能，支持 PDF、图片、Office 文档等格式。
从 mineru_plus 项目迁移而来，代码风格已适配 EngramNote 项目规范。

主要功能：
- PDF/图片/Office 文档 → Markdown 转换
- 支持本地 pipeline 和远程 VLM API 两种解析后端
- 大文件自动分块处理
- Markdown 后处理（去水印、合并空行）
- 标题自动提取
"""

from .converter import convert, convert_folder
from .intake import (
    intake_docx,
    intake_image,
    intake_pdf,
    intake_pptx,
    intake_xlsx,
)
from .models import (
    SUPPORTED_EXTS,
    ConversionResult,
    SourceType,
)

__all__ = [
    "convert",
    "convert_folder",
    "intake_pdf",
    "intake_image",
    "intake_docx",
    "intake_pptx",
    "intake_xlsx",
    "ConversionResult",
    "SourceType",
    "SUPPORTED_EXTS",
]
