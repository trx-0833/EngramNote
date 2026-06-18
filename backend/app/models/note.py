"""
笔记模型模块

本模块定义了笔记数据表模型，是系统的核心数据实体。
笔记追踪文档从上传到归档的完整生命周期，包括状态流转、文件路径和元数据。

主要职责：
- 定义文档来源类型枚举 SourceType（PDF、图片、Office 文档、音视频等）
- 定义笔记状态枚举 NoteStatus（上传中 → 转换中 → 已转换 → 清洗中 → 已清洗 → 学习中 → 已归档）
- 定义笔记表结构，关联用户、存储文件路径、状态和元数据

设计决策：
- 使用枚举类型约束 source_type 和 status，避免无效值
- original_file_path / original_md_path / clean_md_path 存储对象存储中的路径，而非本地路径
- metadata_ 字段使用 JSON 类型（而非 JSONB），兼容 SQLite 和 PostgreSQL
- metadata_ 在 Python 中命名为 metadata_（带下划线），映射到数据库列名为 metadata，
  避免与 SQLAlchemy 的 metadata 属性冲突
- 文件路径格式为 {user_id}/{note_id}/{filename}，确保用户间隔离
"""

import enum
from typing import Any, Dict, Optional

from sqlalchemy import Enum, Integer, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey

from .base import BaseModel


class SourceType(str, enum.Enum):
    """
    文档来源类型枚举

    标识上传文件的类型，决定后续的转换处理方式：
    - PDF/图片/Office 文档 → 调用 Mineru 转换为 Markdown
    - 音视频 → 调用 ASR 引擎转写为文本
    """
    pdf = "pdf"
    image = "image"
    docx = "docx"
    pptx = "pptx"
    xlsx = "xlsx"
    audio = "audio"
    video = "video"


class NoteStatus(str, enum.Enum):
    """
    笔记状态枚举

    定义笔记从上传到归档的完整生命周期状态流转：
    uploading → converting → converted → cleaning → cleaned → learning → archived
    任何阶段都可能进入 failed 状态，清洗阶段可能进入 cleaning_failed 状态，
    学习阶段可能进入 learning_failed 状态。

    状态说明：
    - uploading: 文件正在上传
    - converting: 正在调用 Mineru/ASR 进行格式转换
    - converted: 已成功转换为 Markdown
    - cleaning: 正在对 Markdown 进行清洗优化
    - cleaned: 清洗完成
    - cleaning_failed: 清洗失败（用户可重新触发清洗）
    - learning: 正在进行 AI 学习理解
    - learning_failed: 学习理解失败（用户可重新触发理解）
    - archived: 已归档，处理完成
    - failed: 处理失败（转换阶段），error_message 中记录原因
    """
    uploading = "uploading"       # 上传中
    converting = "converting"     # 转换中（Mineru/ASR 处理）
    converted = "converted"       # 已转换为 Markdown
    cleaning = "cleaning"         # 清洗中
    cleaned = "cleaned"           # 已清洗
    cleaning_failed = "cleaning_failed"  # 清洗失败（与转换失败 failed 区分）
    learning = "learning"         # 学习理解中
    learning_failed = "learning_failed"  # 学习理解失败（用户可重新触发理解）
    archived = "archived"         # 已归档
    failed = "failed"             # 处理失败（转换阶段）


class Note(BaseModel):
    """
    笔记模型

    对应数据库中的 notes 表，是系统的核心数据实体。
    每条笔记代表用户上传的一个文档及其处理状态。

    Attributes:
        id: UUID 主键（继承自 BaseModel）
        user_id: 所属用户 ID，外键关联 users 表
        title: 笔记标题，默认为"未命名笔记"
        source_type: 文件来源类型（PDF/图片/Office/音视频）
        original_file_path: 原始文件在对象存储中的路径
        original_md_path: 转换后的原始 Markdown 在对象存储中的路径
        clean_md_path: 清洗后的 Markdown 在对象存储中的路径（可为空）
        status: 当前处理状态
        file_size: 文件大小（字节）
        page_count: 文档页数（仅 PDF/Office 文档有值）
        error_message: 失败时的错误信息
        metadata_: 扩展元数据（JSON 格式），存储转换结果等附加信息
        created_at: 创建时间（继承自 BaseModel）
        updated_at: 更新时间（继承自 BaseModel）
    """
    __tablename__ = "notes"

    # 所属用户，建立索引加速按用户查询
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True, nullable=False)
    # 笔记标题，默认为"未命名笔记"，可由用户或 AI 修改
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="未命名笔记")
    # 文件来源类型，决定转换处理方式
    source_type: Mapped[SourceType] = mapped_column(Enum(SourceType), nullable=False)
    # 原始文件在对象存储中的路径，格式：{user_id}/{note_id}/{filename}
    original_file_path: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    # Mineru/ASR 转换后生成的原始 Markdown 文件路径
    original_md_path: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    # 经过清洗优化后的 Markdown 文件路径（清洗阶段完成后才有值）
    clean_md_path: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    # 当前处理状态，默认为 uploading
    status: Mapped[NoteStatus] = mapped_column(Enum(NoteStatus), default=NoteStatus.uploading, nullable=False)
    # 文件大小（字节）
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    # 文档页数，仅 PDF/Office 文档有值，音视频为 None
    page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # 处理失败时的错误信息，成功时为 None
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 扩展元数据，使用 JSON 类型兼容 SQLite 和 PostgreSQL
    # Python 属性名用 metadata_（带下划线），数据库列名映射为 metadata
    # 避免与 SQLAlchemy 的 metadata 属性冲突
    metadata_: Mapped[Optional[Dict[str, Any]]] = mapped_column("metadata", JSON, nullable=True)
