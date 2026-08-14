"""
PDF 按页裁剪服务

提供 PDF 页数获取、页范围表达式解析和按页裁剪能力，供上传流程（POST /api/upload/commit）使用。

技术路线（依据项目踩坑记录，见 开发时间表.md "PDF 分块体积膨胀问题"）：
- 裁剪优先使用 PyMuPDF（fitz）的 insert_pdf：精确追踪资源引用，
  只保留页面实际引用的资源，避免裁剪后体积随页数线性膨胀（曾出现 14GB 问题）。
- fitz 不可用时回退 pypdfium2 一次性批量 import_pages（避免逐页循环复制完整资源字典）。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# 单次裁剪允许的最大页数，防止超长输入拖垮服务
MAX_CROP_PAGES = 500

# 页范围表达式单项：单个整数（如 25）或闭区间（如 1-20）
_PAGE_ITEM_RE = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+))?\s*$")


def get_pdf_page_count(path: str) -> int:
    """
    获取 PDF 页数

    优先使用 PyMuPDF（fitz）；不可用时回退 pypdfium2。

    Args:
        path: PDF 文件路径

    Returns:
        int: 页数

    Raises:
        ValueError: 无法解析 PDF 或打开失败
    """
    try:
        import fitz
    except ImportError:
        fitz = None

    if fitz is not None:
        try:
            doc = fitz.open(path)
            try:
                return doc.page_count
            finally:
                doc.close()
        except Exception as e:
            logger.warning("fitz 读取页数失败，回退 pypdfium2: %s", e)

    try:
        import pypdfium2 as pdfium
    except ImportError as e:
        raise ValueError(f"环境中既无 fitz 也无 pypdfium2，无法读取 PDF 页数: {e}") from e

    try:
        doc = pdfium.PdfDocument(path)
        try:
            return len(doc)
        finally:
            doc.close()
    except Exception as e:
        raise ValueError(f"无法读取 PDF 页数: {e}") from e


def parse_page_spec(spec: str, page_count: int) -> list[int]:
    """
    解析页范围表达式为 1-based 页号列表

    支持语法：`1-20,25,30-32`（整数与闭区间用逗号分隔）。

    校验规则：
    - 表达式非空，每项为整数或 a-b 区间（a <= b，否则报错）
    - 页号不越界（1 <= n <= page_count，0 与负号自然被排除）
    - 页数不超过 MAX_CROP_PAGES
    - 结果升序去重返回

    Args:
        spec: 页范围表达式，如 "1-20,25,30-32"
        page_count: PDF 总页数

    Returns:
        list[int]: 升序去重后的页号列表（1-based）

    Raises:
        ValueError: 表达式为空、格式非法、倒序区间或越界时抛出
    """
    if not spec or not spec.strip():
        raise ValueError("页范围不能为空")

    pages: set[int] = set()
    for part in spec.split(","):
        match = _PAGE_ITEM_RE.match(part)
        if not match:
            raise ValueError(f"无效的页范围: '{part.strip()}'")
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) is not None else start
        if start < 1:
            raise ValueError(f"页码必须从 1 开始: '{part.strip()}'")
        if end > page_count:
            raise ValueError(
                f"页码 {end} 超出范围（文档共 {page_count} 页）: '{part.strip()}'"
            )
        if start > end:
            raise ValueError(f"区间起始页不能大于结束页: '{part.strip()}'")
        pages.update(range(start, end + 1))

    if not pages:
        raise ValueError("页范围不能为空")
    if len(pages) > MAX_CROP_PAGES:
        raise ValueError(f"裁剪页数过多（最多 {MAX_CROP_PAGES} 页），请缩小范围")

    return sorted(pages)


def crop_pdf(src_path: str, pages: list[int], out_path: str) -> None:
    """
    按指定页列表裁剪 PDF 并保存到 out_path

    优先使用 PyMuPDF（fitz）insert_pdf——精确追踪资源引用，体积最小；
    fitz 不可用时回退 pypdfium2 一次性批量 import_pages。

    Args:
        src_path: 源 PDF 路径
        pages: 要保留的页号列表（1-based，可为无序，内部会排序）
        out_path: 输出 PDF 路径

    Raises:
        ValueError: 环境缺少 PDF 库或裁剪失败
    """
    try:
        import fitz
    except ImportError:
        fitz = None

    if not pages:
        raise ValueError("裁剪页列表不能为空")

    if fitz is not None:
        _crop_with_fitz(src_path, pages, out_path)
        return

    try:
        import pypdfium2 as pdfium
    except ImportError as e:
        raise ValueError(f"环境中既无 fitz 也无 pypdfium2，无法裁剪 PDF: {e}") from e
    _crop_with_pdfium(pdfium, src_path, pages, out_path)


def _crop_with_fitz(src_path: str, pages: list[int], out_path: str) -> None:
    """使用 PyMuPDF 裁剪：insert_pdf 精确复制指定页面及其引用的资源"""
    import fitz

    ordered = sorted(pages)
    src_doc = fitz.open(src_path)
    out_doc = fitz.open()
    try:
        for page_no in ordered:
            out_doc.insert_pdf(src_doc, from_page=page_no - 1, to_page=page_no - 1)
        out_doc.save(
            out_path,
            garbage=1,
            deflate=True,
            clean=True,
        )
    except Exception as e:
        raise ValueError(f"fitz 裁剪 PDF 失败: {e}") from e
    finally:
        out_doc.close()
        src_doc.close()


def _crop_with_pdfium(pdfium, src_path: str, pages: list[int], out_path: str) -> None:
    """
    使用 pypdfium2 裁剪（fitz 不可用时的回退方案）

    一次性批量 import_pages 并保存，最后尝试用 fitz 清理未引用资源（若可用）。
    """
    ordered = sorted(pages)
    src_doc = pdfium.PdfDocument(src_path)
    out_doc = pdfium.PdfDocument.new()
    try:
        out_doc.import_pages(src_doc, pages=[p - 1 for p in ordered])
        out_doc.save(out_path)
    except Exception as e:
        raise ValueError(f"pypdfium2 裁剪 PDF 失败: {e}") from e
    finally:
        out_doc.close()
        src_doc.close()

    _strip_unused_resources(Path(out_path))


def _strip_unused_resources(pdf_path: Path) -> None:
    """
    清理 PDF 中未被页面引用的资源对象

    pypdfium2 的 import_pages 会复制源文档的完整资源字典到裁剪结果中，
    若 fitz 可用，用 save(clean=True) 自动剔除未引用对象。
    """
    try:
        import fitz
    except ImportError:
        return

    try:
        doc = fitz.open(str(pdf_path))
        tmp_path = str(pdf_path) + ".tmp"
        doc.save(tmp_path, clean=True, deflate=True, garbage=1)
        doc.close()
        import os

        os.replace(tmp_path, str(pdf_path))
    except Exception as e:
        logger.warning("清理裁剪资源失败: %s - %s", pdf_path.name, e)
