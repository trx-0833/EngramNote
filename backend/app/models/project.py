"""
项目模型模块

本模块定义"项目"数据表模型。项目是「项目隔离 + 状态旁载」目录结构的
项目层（Vault 目录中的一级子目录），用于按主题/任务组织用户的学习资料。

主要职责：
- 定义项目表结构，关联用户、项目名称、目录名 slug 和描述
- slug 作为 Vault 目录名，创建后不可变（重命名项目只改显示名）

设计决策：
- slug 每用户唯一，作为存储路径的关键段，避免物理搬移文件
- 项目与用户一对多关系，每个用户可创建多个项目
- 项目与笔记一对多关系，一个项目可包含多个笔记
- 继承 BaseModel 获得 id、created_at、updated_at 公共字段
"""

from typing import Optional

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


class Project(BaseModel):
    """
    项目模型

    对应数据库中的 projects 表。

    Attributes:
        id: UUID 主键（继承自 BaseModel）
        user_id: 所属用户 ID，外键关联 users 表
        name: 项目显示名称，如 "Transformer 论文"
        slug: Vault 目录名（每用户唯一），创建后不可变
        description: 项目描述，可选
        created_at: 创建时间（继承自 BaseModel）
        updated_at: 更新时间（继承自 BaseModel）
    """
    __tablename__ = "projects"

    # 所属用户，建立索引加速按用户查询
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True, nullable=False)
    # 项目显示名称
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Vault 目录名（slug），每用户唯一，创建后不可变
    slug: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    # 项目描述，可选
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
