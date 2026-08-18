"""
笔记-资料关联模型模块

本模块定义笔记与资料之间的多对多关联表。
用户可以将自己的笔记与学习资料建立关联，用于后续的对比评估与标注。

主要职责：
- 定义 note_material_links 关联表，记录 personal_note 与 material_note 的配对关系
- 通过唯一约束防止同一对笔记-资料被重复关联

设计决策：
- 使用独立关联表而非在 notes 表上加外键，支持多对多关系
- personal_note_id 与 material_note_id 均建立索引，加速双向查询
- 外键设置 ON DELETE CASCADE，笔记删除时自动清理关联记录
"""

from typing import Optional

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


class NoteMaterialLink(BaseModel):
    """
    笔记-资料关联模型

    对应数据库中的 note_material_links 表，记录一条用户笔记与一条学习资料之间的关联。

    Attributes:
        id: UUID 主键（继承自 BaseModel）
        user_id: 所属用户 ID，外键关联 users 表
        personal_note_id: 用户笔记 ID，外键关联 notes 表，删除时级联
        material_note_id: 学习资料 ID，外键关联 notes 表，删除时级联
        created_at: 创建时间（继承自 BaseModel）
        updated_at: 更新时间（继承自 BaseModel）
    """
    __tablename__ = "note_material_links"
    __table_args__ = (
        UniqueConstraint("personal_note_id", "material_note_id", name="uq_note_material_link"),
    )

    # 所属用户，建立索引加速按用户查询
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False, index=True)
    # 用户笔记 ID，外键关联 notes 表；物理删除笔记时置 NULL（悬挂引用），双链记录保留
    personal_note_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("notes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 学习资料 ID，外键关联 notes 表；物理删除笔记时置 NULL（悬挂引用），双链记录保留
    material_note_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("notes.id", ondelete="SET NULL"), nullable=True, index=True
    )


if __name__ == "__main__":
    pass
