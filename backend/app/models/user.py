"""
用户模型模块

本模块定义了用户数据表模型，存储用户的基本认证信息。
用户是系统的核心实体，所有笔记和文件都与用户关联。

主要职责：
- 定义用户表结构（邮箱、用户名、密码哈希、激活状态）
- 通过 email 和 username 建立唯一索引，确保登录标识不重复

设计决策：
- 密码以 bcrypt 哈希存储，不保存明文
- email 和 username 均设置唯一索引，支持两种方式登录
- is_active 字段用于软禁用用户，而非物理删除
- 继承 BaseModel 获得 id、created_at、updated_at 公共字段
"""

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


class User(BaseModel):
    """
    用户模型

    对应数据库中的 users 表，存储用户认证和基本信息。

    Attributes:
        id: UUID 主键（继承自 BaseModel）
        email: 用户邮箱，唯一且建立索引，用于登录
        username: 用户名，唯一且建立索引，用于展示
        hashed_password: bcrypt 哈希后的密码，不存储明文
        is_active: 用户是否激活，默认为 True，设为 False 可软禁用用户
        created_at: 创建时间（继承自 BaseModel）
        updated_at: 更新时间（继承自 BaseModel）
    """
    __tablename__ = "users"

    # 邮箱，作为登录凭证之一，必须唯一
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    # 用户名，用于展示，必须唯一
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    # 密码哈希值，由 auth_service.hash_password() 生成
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    # 用户激活状态，设为 False 可禁止用户登录而不删除数据
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
