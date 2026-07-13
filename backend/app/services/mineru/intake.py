"""
MinerU 文件摄入模块

负责各类文件格式的预处理（摄入），包括：
- PDF: 验证、大文件自动分块
- 图片: 转 PDF（供 MinerU pipeline 处理）
- DOCX/PPTX/XLSX: 验证后直接透传

分块策略：当 PDF 页数超过 chunk_size 时，自动拆分为多个子 PDF，
支持 chunk_overlap 页重叠以确保跨页内容不丢失。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple

import pypdfium2 as pdfium
from PIL import Image

from .config_loader import DEFAULT_CONFIG

import logging

logger = logging.getLogger(__name__)


def intake_pdf(
    file_path: Path,
    chunk_size: int = DEFAULT_CONFIG["chunk_size"],
    chunk_overlap: int = DEFAULT_CONFIG["chunk_overlap"],
) -> Tuple[List[Path], Optional[Path]]:
    """
    PDF 文件摄入

    小文件直接透传，大文件自动分块。

    Args:
        file_path: PDF 文件路径
        chunk_size: 分块大小（页数），默认 200
        chunk_overlap: 分块重叠页数，默认 10

    Returns:
        (待处理文件路径列表, 临时目录路径或 None)
    """
    _validate_pdf(file_path)

    doc = pdfium.PdfDocument(str(file_path))
    try:
        page_count = len(doc)
    finally:
        doc.close()

    if page_count <= chunk_size:
        logger.info(f"PDF页数({page_count}) <= 分块大小({chunk_size})，直接透传")
        return [file_path], None

    logger.info(f"PDF页数({page_count}) > 分块大小({chunk_size})，自动分块")
    chunks, temp_dir = _split_pdf(file_path, page_count, chunk_size, chunk_overlap)
    logger.info(f"PDF分块完成: {len(chunks)}块 (大小={chunk_size}, 重叠={chunk_overlap})")
    return chunks, temp_dir


def intake_image(file_path: Path) -> Tuple[Path, Optional[Path]]:
    """
    图片文件摄入

    将图片转换为 PDF，供 MinerU pipeline 处理。

    Args:
        file_path: 图片文件路径

    Returns:
        (转换后的 PDF 路径, 临时目录路径)
    """
    _validate_image(file_path)

    temp_dir = Path(tempfile.mkdtemp(prefix="mineru_intake_image_"))
    pdf_path = temp_dir / f"{file_path.stem}.pdf"

    with Image.open(file_path) as img:
        if img.mode != "RGB":
            img = img.convert("RGB")
        dpi = _compute_image_dpi(img)
        img.save(str(pdf_path), "PDF", resolution=dpi)

    logger.info(f"图片 {file_path.name} -> PDF: {pdf_path} ({dpi}DPI)")
    return pdf_path, temp_dir


def intake_docx(file_path: Path) -> Path:
    """DOCX 文件摄入：验证后直接透传"""
    _validate_docx(file_path)
    logger.info(f"DOCX文件直接透传: {file_path}")
    return file_path


def intake_pptx(file_path: Path) -> Path:
    """PPTX 文件摄入：验证后直接透传"""
    _validate_pptx(file_path)
    logger.info(f"PPTX文件直接透传: {file_path}")
    return file_path


def intake_xlsx(file_path: Path) -> Path:
    """XLSX 文件摄入：验证后直接透传"""
    _validate_xlsx(file_path)
    logger.info(f"XLSX文件直接透传: {file_path}")
    return file_path


# ── 内部辅助函数 ──────────────────────────────────────────────


def _check_file_exists_and_nonempty(file_path: Path) -> None:
    """检查文件是否存在且非空"""
    if not file_path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")
    if file_path.stat().st_size == 0:
        raise ValueError(f"文件为空: {file_path}")


def _validate_pdf(file_path: Path) -> None:
    """验证 PDF 文件：存在、非空、未加密、未损坏"""
    _check_file_exists_and_nonempty(file_path)
    try:
        doc = pdfium.PdfDocument(str(file_path))
        doc.close()
    except pdfium.PdfiumError as e:
        error_msg = str(e)
        if "password" in error_msg.lower() or "encrypt" in error_msg.lower():
            raise ValueError(f"PDF文件已加密，请解密后再处理: {file_path}")
        raise ValueError(f"PDF文件无效或损坏: {file_path} - {e}")
    except Exception as e:
        raise ValueError(f"PDF文件无效或损坏: {file_path} - {e}")


def _validate_image(file_path: Path) -> None:
    """验证图片文件"""
    _check_file_exists_and_nonempty(file_path)
    try:
        Image.open(file_path).verify()
    except Exception as e:
        raise ValueError(f"图片文件无效或损坏: {file_path} - {e}")


def _validate_docx(file_path: Path) -> None:
    """验证 DOCX 文件"""
    _check_file_exists_and_nonempty(file_path)
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            if "word/document.xml" not in zf.namelist():
                raise ValueError("无效的DOCX文件")
    except zipfile.BadZipFile:
        raise ValueError(f"DOCX文件无效或损坏: {file_path}")
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"DOCX文件校验失败: {file_path} - {e}")


def _validate_pptx(file_path: Path) -> None:
    """验证 PPTX 文件"""
    _check_file_exists_and_nonempty(file_path)
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            if "ppt/presentation.xml" not in zf.namelist():
                raise ValueError("无效的PPTX文件")
    except zipfile.BadZipFile:
        raise ValueError(f"PPTX文件无效或损坏: {file_path}")
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"PPTX文件校验失败: {file_path} - {e}")


def _validate_xlsx(file_path: Path) -> None:
    """验证 XLSX 文件"""
    _check_file_exists_and_nonempty(file_path)
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            if "xl/workbook.xml" not in zf.namelist():
                raise ValueError("无效的XLSX文件")
    except zipfile.BadZipFile:
        raise ValueError(f"XLSX文件无效或损坏: {file_path}")
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"XLSX文件校验失败: {file_path} - {e}")


def _split_pdf(
    file_path: Path, page_count: int, chunk_size: int, chunk_overlap: int
) -> Tuple[List[Path], Optional[Path]]:
    """
    PDF 分块核心函数

    优先使用 PyMuPDF (fitz) 分块，它能精确追踪资源引用，
    避免资源冗余导致分块体积膨胀。PyMuPDF 不可用时回退到 pypdfium2。
    """
    output_dir = Path(tempfile.mkdtemp(prefix="mineru_chunk_"))
    chunks: List[Path] = []

    use_fitz = _can_use_fitz()

    if use_fitz:
        chunks = _split_pdf_fitz(file_path, page_count, chunk_size, chunk_overlap, output_dir)
    else:
        chunks = _split_pdf_pdfium(file_path, page_count, chunk_size, chunk_overlap, output_dir)

    return chunks, output_dir


def _can_use_fitz() -> bool:
    """检查 PyMuPDF 是否可用"""
    try:
        import fitz
        return True
    except ImportError:
        return False


def _split_pdf_fitz(
    file_path: Path, page_count: int, chunk_size: int, chunk_overlap: int, output_dir: Path
) -> List[Path]:
    """
    使用 PyMuPDF 分块 PDF

    PyMuPDF 的 insert_pdf 精确复制指定页面及其引用的资源，
    配合 save(garbage=1) 合并重复对象，分块体积最小。
    """
    import fitz

    chunks: List[Path] = []
    src_doc = fitz.open(str(file_path))

    try:
        start_page = 0
        chunk_index = 0
        while start_page < page_count:
            end_page = min(start_page + chunk_size, page_count)

            chunk_doc = fitz.open()
            try:
                chunk_doc.insert_pdf(src_doc, from_page=start_page, to_page=end_page - 1)

                chunk_path = output_dir / f"{file_path.stem}_chunk_{chunk_index:04d}.pdf"
                chunk_doc.save(
                    str(chunk_path),
                    garbage=1,
                    deflate=True,
                    clean=True,
                )
            finally:
                chunk_doc.close()

            chunks.append(chunk_path)
            logger.debug(
                f"分块 {chunk_index}: 页 {start_page+1}-{end_page} -> {chunk_path.name}"
            )

            chunk_index += 1
            if end_page >= page_count:
                break
            start_page = end_page - chunk_overlap
    except Exception:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise
    finally:
        src_doc.close()

    return chunks


def _split_pdf_pdfium(
    file_path: Path, page_count: int, chunk_size: int, chunk_overlap: int, output_dir: Path
) -> List[Path]:
    """
    使用 pypdfium2 分块 PDF（PyMuPDF 不可用时的回退方案）

    注意：pypdfium2 的 import_pages 会复制源文档的完整资源字典，
    包括未被页面引用的资源，导致分块体积偏大。
    """
    chunks: List[Path] = []

    doc = pdfium.PdfDocument(str(file_path))
    try:
        start_page = 0
        chunk_index = 0
        while start_page < page_count:
            end_page = min(start_page + chunk_size, page_count)

            chunk_doc = pdfium.PdfDocument.new()
            try:
                page_indices = list(range(start_page, end_page))
                chunk_doc.import_pages(doc, pages=page_indices)

                chunk_path = output_dir / f"{file_path.stem}_chunk_{chunk_index:04d}.pdf"
                chunk_doc.save(str(chunk_path))
            finally:
                chunk_doc.close()

            _strip_unused_resources(chunk_path)

            chunks.append(chunk_path)
            logger.debug(
                f"分块 {chunk_index}: 页 {start_page+1}-{end_page} -> {chunk_path.name}"
            )

            chunk_index += 1
            if end_page >= page_count:
                break
            start_page = end_page - chunk_overlap
    except Exception:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise
    finally:
        doc.close()

    return chunks


def _strip_unused_resources(pdf_path: Path) -> None:
    """
    清理 PDF 中未被页面引用的资源对象

    pypdfium2 的 import_pages 会复制源文档的完整资源字典到分块中，
    PyMuPDF 的 save(clean=True) 可自动剔除未引用对象。
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
        os.replace(tmp_path, str(pdf_path))
    except Exception as e:
        logger.warning(f"清理分块资源失败: {pdf_path.name} - {e}")


def _compute_image_dpi(img: Image.Image) -> float:
    """根据图片尺寸计算合适的 DPI"""
    dpi = img.info.get("dpi")
    if dpi:
        if isinstance(dpi, tuple):
            return max(dpi[0], dpi[1])
        return float(dpi)
    width, height = img.size
    if width >= 3000 or height >= 3000:
        return 150.0
    if width >= 1500 or height >= 1500:
        return 200.0
    return 300.0
