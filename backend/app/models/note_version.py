"""
笔记版本历史模型模块

本模块定义笔记 Markdown 内容的版本历史数据表，用于支持版本追踪、
历史查看、版本对比与历史版本恢复。

主要职责：
- 定义版本来源枚举 VersionSource（用户编辑 / 自动清洗 / 系统）
- 定义 note_versions 表结构，关联笔记与用户，记录版本号、来源、内容大小与存储路径

设计决策：
- 外键设置 ON DELETE CASCADE，笔记或用户删除时自动清理版本记录
- version_number 为整数递增，按 note_id 维度独立编号
- storage_path 指向对象存储中的版本快照文件，避免内容冗余存入数据库
- source 使用 String 而非 Enum 列类型，便于跨 SQLite / PostgreSQL 兼容
"""

import enum
from typing import Optional

from sqlalchemy import String, Integer, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


class VersionSource(str, enum.Enum):
    """
    版本来源枚举

    标识版本快照的产生途径：
    - USER_EDIT: 用户手动编辑保存触发
    - AUTO_CLEAN: 系统清洗任务覆盖前自动触发
    - SYSTEM: 其他系统级操作触发
    """
    USER_EDIT = "user_edit"
    AUTO_CLEAN = "auto_clean"
    SYSTEM = "system"


class NoteVersion(BaseModel):
    """
    笔记版本历史模型

    对应数据库中的 note_versions 表，记录笔记 Markdown 内容的每次版本快照。

    Attributes:
        id: UUID 主键（继承自 BaseModel）
        note_id: 所属笔记 ID，外键关联 notes 表，删除时级联
        user_id: 所属用户 ID，外键关联 users 表，删除时级联
        version_number: 版本序号，按 note_id 维度从 1 递增
        source: 版本来源（user_edit / auto_clean / system）
        content_size: 版本内容字节大小（UTF-8 编码）
        change_summary: 变更摘要文本（如 "+5 -2 lines"），可选
        storage_path: 版本快照在对象存储中的路径
        created_at: 创建时间（继承自 BaseModel）
        updated_at: 更新时间（继承自 BaseModel）
    """
    __tablename__ = "note_versions"

    # 所属笔记，外键关联 notes 表，删除时级联
    note_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("notes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # 所属用户，外键关联 users 表，删除时级联
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # 版本序号，按 note_id 维度从 1 递增
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # 版本来源：user_edit / auto_clean / system
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default=VersionSource.USER_EDIT.value
    )
    # 版本内容字节大小（UTF-8 编码）
    content_size: Mapped[int] = mapped_column(Integer, default=0)
    # 变更摘要，例如 "+5 -2 lines"
    change_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 版本快照在对象存储中的路径
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)


if __name__ == "__main__":
    pass
