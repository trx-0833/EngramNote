"""
文件夹模型模块

本模块定义了每日学习资料文件夹的数据表模型。
文件夹用于按日期组织用户上传的学习资料，便于管理和查找。

主要职责：
- 定义文件夹表结构，关联用户、文件夹名称、描述和日期
- 通过 folder_date 建立索引，加速按日期查询

设计决策：
- folder_date 使用带时区的 DateTime，确保跨时区一致性
- 文件夹与用户一对多关系，每个用户可创建多个文件夹
- 文件夹与笔记一对多关系，一个文件夹可包含多个笔记
- 继承 BaseModel 获得 id、created_at、updated_at 公共字段
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel, TZDateTime


class Folder(BaseModel):
    """
    文件夹模型

    对应数据库中的 folders 表，用于按日期组织用户上传的学习资料。

    Attributes:
        id: UUID 主键（继承自 BaseModel）
        user_id: 所属用户 ID，外键关联 users 表
        name: 文件夹名称，如 "2024-01-15 学习资料"
        description: 文件夹描述，可选
        folder_date: 文件夹日期，用于按日期分组和排序
        created_at: 创建时间（继承自 BaseModel）
        updated_at: 更新时间（继承自 BaseModel）
    """
    __tablename__ = "folders"

    # 所属用户，建立索引加速按用户查询
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True, nullable=False)
    # 文件夹名称
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    # 文件夹描述，可选
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 文件夹日期，建立索引加速按日期查询
    folder_date: Mapped[datetime] = mapped_column(TZDateTime(timezone=True), nullable=False, index=True)
