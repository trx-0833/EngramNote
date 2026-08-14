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
        # 项目标签数组（多对多），需在调用前注入 note._project_ids/_project_names
        # （镜像非权威，调用方未注入时为空数组）
        "project_ids": getattr(note, "_project_ids", None) or [],
        "project_names": getattr(note, "_project_names", None) or [],
        "filename": parts[-1] if parts else None,
        "folder_id": note.folder_id,
        # 文件夹名需在调用前注入 note._folder_name（镜像非权威，缺失时为 None）
        "folder_name": getattr(note, "_folder_name", None),
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


def write_project_meta(user_id: str, projects: list) -> None:
    """
    将用户的全部项目（标签）清单写穿到 {user_id}/inbox/output/meta/projects.json

    项目为纯标签后不再拥有独立目录，清单写到用户级收件箱下，
    供人工浏览 Vault 目录或脱离数据库时快速查看 id→name 映射。
    项目创建/更新/删除后同步，写入失败只记录日志，不影响业务主流程（镜像非权威）。

    Args:
        user_id: 用户 ID
        projects: 该用户的全部 Project 模型实例列表
    """
    try:
        content = {
            "projects": [
                {
                    "id": project.id,
                    "name": project.name,
                    "description": project.description,
                    "created_at": _iso(project.created_at),
                    "updated_at": _iso(project.updated_at),
                }
                for project in projects
            ]
        }
        data = json.dumps(content, ensure_ascii=False, indent=2).encode("utf-8")
        upload_bytes(
            settings.minio_bucket_markdown,
            f"{vault_path.inbox_prefix(user_id)}/{vault_path.META_DIR}/projects.json",
            data,
            content_type="application/json",
        )
    except Exception:
        logger.exception("Vault 项目清单写穿失败: user_id=%s", user_id)
