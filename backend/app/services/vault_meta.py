"""
Vault 状态旁载模块

按共识「状态不撒谎法则」实现 meta JSON 写穿镜像：
- DB（notes 表）仍是应用的状态权威源，前端轮询不变；
- 每次笔记状态变更时，同步把当前全量状态写为 {P}/output/meta/{base}.json，
  作为物理备份，供人工浏览 Vault 目录或脱离数据库时快速查看状态。

meta 写入失败只记录日志，不影响业务主流程（镜像非权威）。
"""

import json
import logging
from datetime import datetime
from typing import Optional

from ..config import get_settings
from ..models.note import Note
from ..services.storage_service import upload_bytes
from . import vault_path

logger = logging.getLogger(__name__)
settings = get_settings()


def _iso(value: Optional[datetime]) -> Optional[str]:
    """将 datetime 序列化为 ISO 字符串"""
    if value is None:
        return None
    return value.isoformat()


def _build_meta(note: Note) -> Optional[dict]:
    """
    组装 meta JSON 内容

    原始文件路径缺失时返回 None（笔记尚未落盘，无需旁载）。
    """
    if not note.original_file_path:
        return None
    metadata = note.metadata_ or {}
    parts = note.original_file_path.split("/")
    return {
        "note_id": note.id,
        "project_id": note.project_id,
        "project_slug": parts[1] if len(parts) >= 2 else None,
        "filename": parts[-1] if parts else None,
        "source_type": note.source_type.value if note.source_type else None,
        "status": note.status.value if note.status else None,
        "file_hash": metadata.get("file_hash"),
        "file_size": note.file_size,
        "page_count": note.page_count,
        "progress": None,  # 当前系统无真实进度数据，保持"状态不撒谎"
        "error_message": note.error_message,
        "original_file_path": note.original_file_path,
        "original_md_path": note.original_md_path,
        "clean_md_path": note.clean_md_path,
        "created_at": _iso(note.created_at),
        "updated_at": _iso(note.updated_at),
    }


def write_note_meta(note: Note):
    """
    将笔记当前状态写穿到 output/meta/{base}.json

    Args:
        note: Note 模型实例（需已包含最新状态）
    """
    try:
        content = _build_meta(note)
        if content is None:
            return
        prefix = vault_path.derive_prefix(note)
        base = vault_path.derive_base(note)
        if not prefix or not base:
            return
        data = json.dumps(content, ensure_ascii=False, indent=2).encode("utf-8")
        upload_bytes(
            settings.minio_bucket_markdown,
            vault_path.meta_object(prefix, base),
            data,
            content_type="application/json",
        )
    except Exception:
        logger.exception("Vault meta 写穿失败: note_id=%s", note.id)
