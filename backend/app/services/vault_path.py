"""
Vault 路径约定模块

定义「项目隔离 + 状态旁载」目录结构的 object-name 约定（POSIX 字符串，
本地文件系统与 MinIO 模式共用）。每条笔记归属于一个项目目录：

    {user_id}/{project_slug}/
    ├── source/{base}{ext}                    # 只读区：原始文件
    ├── output/markdown/{base}.md             # 生成区：原始转换（只读）
    ├── output/markdown/{base}.clean.md       # 生成区：清洗副本（工作副本）
    ├── output/meta/{base}.json               # 状态旁载（写穿镜像）
    ├── history/versions/v{N}.md              # 版本区：手动编辑大版本归档
    ├── output/assets/                        # 预留（进阶能力，暂不写入）
    └── cache/                                # 预留（OCR/ASR 缓存区）

命名关联法则：source/{base}{ext} ↔ output/markdown/{base}.md（仅扩展名不同），
保证脱离数据库也能通过文件名完成溯源。
"""

import re
from pathlib import PurePosixPath

# 项目目录结构常量（object-name 片段）
SOURCE_DIR = "source"
OUTPUT_DIR = "output"
MARKDOWN_DIR = "output/markdown"
ASSETS_DIR = "output/assets"
META_DIR = "output/meta"
HISTORY_DIR = "history"
VERSIONS_DIR = "history/versions"
CACHE_DIR = "cache"

# 创建项目时需在磁盘/对象存储中预建的项目目录树
# 对应共识目录树：source/、output/markdown/、output/assets/、output/meta/、history/versions/、cache/
PROJECT_SUBDIRS = (
    SOURCE_DIR,
    MARKDOWN_DIR,
    ASSETS_DIR,
    META_DIR,
    VERSIONS_DIR,
    CACHE_DIR,
)

# 文件扩展名到来源类型的映射（upload 与扫描导入共用，单一来源）
EXT_TO_SOURCE_TYPE = {
    ".pdf": "pdf",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".docx": "docx",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
    ".mp4": "video",
    ".mkv": "video",
    ".mov": "video",
    ".mp3": "audio",
    ".wav": "audio",
    ".m4a": "audio",
    ".flac": "audio",
    ".ogg": "audio",
    ".aac": "audio",
    ".md": "markdown",
}

# 允许的扩展名集合（上传校验与扫描导入共用）
ALLOWED_EXTS = set(EXT_TO_SOURCE_TYPE.keys())


def sanitize_slug(name: str, max_len: int = 60) -> str:
    """
    将项目名清洗为安全的目录名（slug）

    保留字母、数字、中文、下划线与连字符，其余字符替换为 "-"。
    项目创建后 slug 不可变（作为 Vault 目录名，重命名项目不迁移文件）。

    Args:
        name: 原始项目名
        max_len: slug 最大长度

    Returns:
        str: 清洗后的 slug
    """
    slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", str(name))
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    slug = slug[:max_len]
    return slug or "project"


def project_prefix(user_id: str, slug: str) -> str:
    """项目前缀：{user_id}/{project_slug}"""
    return f"{user_id}/{slug}"


# 未归属项目笔记的物理兜底前缀，非真实项目行
INBOX_SLUG = "inbox"


def inbox_prefix(user_id: str) -> str:
    """收件箱前缀：{user_id}/inbox，未归属任何项目的笔记物理存储兜底前缀（非真实项目行）"""
    return project_prefix(user_id, INBOX_SLUG)


def source_object(prefix: str, base: str, ext: str) -> str:
    """原始文件对象名：{P}/source/{base}{ext}"""
    return f"{prefix}/{SOURCE_DIR}/{base}{ext}"


def markdown_object(prefix: str, base: str) -> str:
    """原始 Markdown 对象名：{P}/output/markdown/{base}.md"""
    return f"{prefix}/{MARKDOWN_DIR}/{base}.md"


def clean_object(prefix: str, base: str) -> str:
    """清洗 Markdown 对象名：{P}/output/markdown/{base}.clean.md"""
    return f"{prefix}/{MARKDOWN_DIR}/{base}.clean.md"


def meta_object(prefix: str, base: str) -> str:
    """状态旁载对象名：{P}/output/meta/{base}.json"""
    return f"{prefix}/{META_DIR}/{base}.json"


def history_object(prefix: str, version_no: int) -> str:
    """版本归档对象名：{P}/history/versions/v{N}.md"""
    return f"{prefix}/{VERSIONS_DIR}/v{version_no}.md"


def derive_prefix(note) -> str:
    """
    从笔记的原始文件路径推导项目前缀 {user_id}/{project_slug}

    Args:
        note: Note 模型实例

    Returns:
        str: 项目前缀；路径异常时返回空串
    """
    parts = (note.original_file_path or "").split("/")
    return "/".join(parts[:2]) if len(parts) >= 2 else ""


def derive_base(note) -> str:
    """
    从笔记的原始文件路径推导文件主干（不含扩展名）

    Args:
        note: Note 模型实例

    Returns:
        str: 文件主干
    """
    return PurePosixPath(note.original_file_path or "").stem
