"""
基础模型模块

本模块定义了所有 ORM 模型的公共基类 BaseModel，
提供每个数据表通用的主键和时间戳字段，避免在各模型中重复定义。

主要职责：
- 定义 UUID 字符串主键 id（自动生成）
- 定义创建时间 created_at（数据库服务器自动填充）
- 定义更新时间 updated_at（创建时自动填充，更新时自动刷新）

设计决策：
- 使用 UUID 字符串而非自增整数作为主键，避免暴露数据量和业务信息
- 时间戳使用带时区的 DateTime，确保跨时区一致性
- created_at 使用 server_default 由数据库生成，避免应用层时钟不一致
- updated_at 使用 onupdate=func.now() 在记录更新时自动刷新
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, TypeDecorator, func
from sqlalchemy.orm import Mapped, mapped_column


from ..database import Base


class TZDateTime(TypeDecorator):
    """
    带 UTC 语义的 DateTime 类型装饰器

    SQLite 本身不存储时区：CURRENT_TIMESTAMP / func.now() 生成的是无时区的
    UTC 时间字符串。本装饰器在读取时为无时区值补上 UTC 时区，在写入时把
    带时区的值统一归一化为无时区 UTC（与 CURRENT_TIMESTAMP 字符串可直接比较）；
    PostgreSQL 的 timestamptz 自带时区，直接透传。

    应用统一以 UTC 存储、展示时再转换为本地时区（如 GMT+8）。
    """
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
            if dialect.name == "sqlite":
                value = value.replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None and dialect.name == "sqlite":
            value = value.replace(tzinfo=timezone.utc)
        return value


class BaseModel(Base):
    """
    所有模型的基类，提供 id / created_at / updated_at 公共字段

    该类标记为 __abstract__ = True，不会创建对应的数据表，
    而是作为其他模型的父类，子类会继承这些公共字段。

    Attributes:
        id: UUID 字符串主键，自动生成，全局唯一
        created_at: 记录创建时间，由数据库服务器在插入时自动设置
        updated_at: 记录更新时间，创建时自动设置，每次更新时自动刷新
    """
    __abstract__ = True

    # UUID 字符串主键，使用 lambda 延迟计算确保每次生成唯一值
    id: Mapped[str] = mapped_column(
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    # 创建时间，使用数据库服务器时间（server_default=func.now()）确保一致性
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(timezone=True),
        server_default=func.now(),
    )
    # 更新时间，创建时默认为当前时间，记录更新时自动刷新
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


if __name__ == "__main__":
    pass
