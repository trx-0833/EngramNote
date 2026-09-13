"""上传安全护栏（overhaul-plan 阶段 6.4）

三件在"读文件"这一层就该拦下的事，都做成**纯函数**：输入是统计量，
输出是"拒绝理由或 None"。这样它们不依赖 HTTP、数据库与真实上传流程，
可以逐条验证边界；接线留在 `api/upload.py`。

## 各自的威胁与判据

| 威胁 | 判据 | 为什么这样定 |
|---|---|---|
| **zip 炸弹** | 中央目录里的 `file_size` 之和、压缩比、条目数 | Office 文档（docx/pptx/xlsx）就是 ZIP 容器，转换阶段会解压它。恶意的 42KB 压缩包能解出 4GB |
| **超大 PDF** | 页数上限 | 页数直接决定 MinerU 与后续 LLM 的工作量；一本 3000 页的书会把理解管道拖垮（成本与耗时都不可控） |
| **无限上传** | 单用户笔记数上限 | 容量配额（MB）挡不住"传一万个小文件"：每个文件都要建笔记、进队列、占 inode |

## 关键实现约束：检查本身不能成为攻击面

zip 炸弹检查**只读中央目录**（`ZipInfo.file_size`），**绝不解压** ——
否则"检查"就变成了"帮攻击者解压"。`zipfile` 读中央目录只按需 seek，
不会把内容读进内存。

另外，畸形 ZIP 会让 `zipfile` 自己抛异常（`BadZipFile`）：这时**拒绝**，
理由写"压缩包结构损坏" —— 一个连目录都读不出来的 Office 文档，
转换阶段同样会失败，早拒比晚拒好。
"""

import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

#: 需要按 ZIP 容器检查的扩展名（Office 文档都是 zip）
ARCHIVE_EXTS = (".docx", ".pptx", ".xlsx")


@dataclass(frozen=True)
class ArchiveStats:
    """ZIP 容器的统计量（全部来自中央目录，不涉及解压）"""

    entries: int
    total_uncompressed_bytes: int
    total_compressed_bytes: int

    @property
    def compression_ratio(self) -> float:
        """压缩比 = 解压后 / 压缩后。压缩后为 0 时按 0 处理（避免除零）"""
        if self.total_compressed_bytes <= 0:
            return 0.0
        return self.total_uncompressed_bytes / self.total_compressed_bytes


def archive_stats(path: Path) -> Optional[ArchiveStats]:
    """读取 ZIP 容器的统计量；不是合法 ZIP 时返回 None

    ⚠️ 只读中央目录，不解压（见模块说明）。
    """
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
    except (zipfile.BadZipFile, OSError) as exc:
        logger.warning("读取压缩包目录失败（按损坏处理）: file=%s, error=%s", path.name, exc)
        return None

    return ArchiveStats(
        entries=len(infos),
        total_uncompressed_bytes=sum(max(0, i.file_size) for i in infos),
        total_compressed_bytes=sum(max(0, i.compress_size) for i in infos),
    )


def check_archive(
    path: Path,
    *,
    max_uncompressed_mb: int,
    max_compression_ratio: int,
    max_entries: int,
) -> Optional[str]:
    """检查 ZIP 容器是否像 zip 炸弹；返回用户可见的拒绝理由，通过则返回 None

    三个阈值都是 `<= 0` 表示不启用该条（便于单独关掉某一条）。
    """
    stats = archive_stats(path)
    if stats is None:
        return "压缩包结构损坏或不是有效的 Office 文档，无法解析"

    if max_entries > 0 and stats.entries > max_entries:
        return f"压缩包内文件数量过多（{stats.entries} > {max_entries}），已拒绝"

    limit_bytes = max_uncompressed_mb * 1024 * 1024
    if max_uncompressed_mb > 0 and stats.total_uncompressed_bytes > limit_bytes:
        return (
            f"解压后体积过大（{stats.total_uncompressed_bytes // (1024 * 1024)}MB > "
            f"{max_uncompressed_mb}MB），已拒绝"
        )

    if max_compression_ratio > 0 and stats.compression_ratio > max_compression_ratio:
        return (
            f"压缩比异常（{stats.compression_ratio:.0f}:1 > {max_compression_ratio}:1），"
            "疑似压缩炸弹，已拒绝"
        )
    return None


def check_pdf_pages(page_count: Optional[int], *, max_pages: int) -> Optional[str]:
    """PDF 页数上限；`max_pages <= 0` 表示不限"""
    if max_pages <= 0 or page_count is None:
        return None
    if page_count > max_pages:
        return f"PDF 页数过多（{page_count} > {max_pages}），请拆分后上传"
    return None


def check_note_count(existing: int, *, max_notes: int) -> Optional[str]:
    """单用户笔记数上限；`max_notes <= 0` 表示不限

    与容量配额（MB）互补：容量配额挡不住"一万个小文件"，
    而每个文件都要建笔记、进转换队列、占 inode。
    """
    if max_notes <= 0:
        return None
    if existing >= max_notes:
        return f"笔记数量已达上限（{max_notes}），请先清理回收站或删除不再需要的笔记"
    return None


__all__ = [
    "ARCHIVE_EXTS",
    "ArchiveStats",
    "archive_stats",
    "check_archive",
    "check_note_count",
    "check_pdf_pages",
]
