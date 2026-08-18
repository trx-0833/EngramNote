"""
Vault 路径约定模块

定义「统一收件箱 + 状态旁载」目录结构的 object-name 约定（POSIX 字符串，
本地文件系统与 MinIO 模式共用）。项目为纯标签归属（note_projects 多对多），
不参与物理路径；所有笔记统一落在收件箱前缀下：

    {user_id}/inbox/
    ├── source/{base}{ext}                    # 只读区：原始文件
    ├── output/markdown/{base}.md             # 生成区：原始转换（只读）
    ├── output/markdown/{base}.clean.md       # 生成区：清洗副本（工作副本）
    ├── output/meta/{base}.json               # 状态旁载（写穿镜像）
    ├── output/meta/projects.json             # 用户级项目标签清单（镜像）
    ├── history/versions/{note_id}/v{N}.md  # 版本区：手动编辑大版本归档（按笔记隔离）
    ├── output/assets/                        # 预留（进阶能力，暂不写入）
    └── cache/                                # 预留（OCR/ASR 缓存区）

命名关联法则：source/{base}{ext} ↔ output/markdown/{base}.md（仅扩展名不同），
保证脱离数据库也能通过文件名完成溯源。
"""

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


# 所有笔记的物理兜底前缀段（标签化后不再有项目 slug 目录）
INBOX_SLUG = "inbox"

# 回收站物理隔离目录段（vault 路径不含 note_id，同名文件会互相覆盖，
# 移入回收站时把文件搬到 {user_id}/trash/{note_id}/ 下隔离，恢复时再搬回）
TRASH_SLUG = "trash"


def inbox_prefix(user_id: str) -> str:
    """收件箱前缀：{user_id}/inbox，所有笔记（无论是否打项目标签）的物理存储兜底前缀（非真实项目行）"""
    return f"{user_id}/{INBOX_SLUG}"


def trash_prefix(user_id: str, note_id: str) -> str:
    """回收站前缀：{user_id}/trash/{note_id}

    移入回收站时把笔记的物理文件搬到此前缀下（source/output 子结构与
    inbox 一致），恢复时搬回；按 note_id 隔离，天然无同名冲突。
    """
    return f"{user_id}/{TRASH_SLUG}/{note_id}"


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


def history_object(prefix: str, note_id: str, version_no: int) -> str:
    """版本归档对象名：{P}/history/versions/{note_id}/v{N}.md

    以 note_id 作为子目录维度，避免不同笔记的同号版本文件互相覆盖。
    """
    return f"{prefix}/{VERSIONS_DIR}/{note_id}/v{version_no}.md"


def derive_prefix(note) -> str:
    """
    推导笔记的物理前缀 {user_id}/inbox

    标签化后所有笔记统一落在收件箱前缀下（不再有项目 slug 目录）；
    用户 id 优先取 note.user_id，兜底解析 original_file_path 第一段。

    Args:
        note: Note 模型实例

    Returns:
        str: {user_id}/inbox；路径异常时返回空串
    """
    user_id = getattr(note, "user_id", None)
    if not user_id:
        parts = (note.original_file_path or "").split("/")
        user_id = parts[0] if parts else ""
    return inbox_prefix(user_id) if user_id else ""


def derive_base(note) -> str:
    """
    从笔记的原始文件路径推导文件主干（不含扩展名）

    Args:
        note: Note 模型实例

    Returns:
        str: 文件主干
    """
    return PurePosixPath(note.original_file_path or "").stem
