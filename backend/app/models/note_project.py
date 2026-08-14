"""
笔记-项目多对多关联模型模块

本模块定义笔记与项目的标签关联表。项目从"Vault 第一层目录（slug）"演化为
"纯标签归属"后，一篇笔记可属于多个项目（标签），通过本表承载多对多关系。

主要职责：
- 记录笔记与项目的多对多关联（标签）
- 唯一约束 (note_id, project_id)，防止重复标签
- 外键 CASCADE：笔记或项目删除时自动清理关联行
"""

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


class NoteProject(BaseModel):
    """
    笔记-项目标签关联模型

    Attributes:
        note_id: 笔记 ID，外键关联 notes 表，删除时级联
        project_id: 项目 ID，外键关联 projects 表，删除时级联
        user_id: 所属用户 ID，外键关联 users 表，删除时级联
    """
    __tablename__ = "note_projects"
    __table_args__ = (
        UniqueConstraint("note_id", "project_id", name="uq_note_projects_note_project"),
    )

    note_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("notes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
