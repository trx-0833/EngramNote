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

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel, TZDateTime


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
        email_reminder_enabled: 邮件提醒开关，默认开启
        last_reminded_at: 最近一次提醒发送时间，未发送过为 NULL
        locale: 界面语言偏好（`zh-CN` / `en`），供 Celery worker 取用（见字段注释）
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
    # 邮件提醒开关（用户级，默认开启，保持"全局开启即发"的向后兼容）
    email_reminder_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # 最近一次提醒发送时间（未发送过为 NULL，用于展示与去重）
    last_reminded_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(timezone=True), nullable=True)
    # 界面语言偏好（i18n 阶段 0）。
    #
    # ## 为什么这个字段必须存在（而不是只靠 `Accept-Language`）
    #
    # LLM 提示词要跟界面语言走（"英文界面下生成的卡片/题目也应是英文"），
    # 而提示词的调用点在 **Celery worker** 里 —— 那里**没有请求上下文**，
    # 读不到 `Accept-Language`（任务签名只有 `note_id`，
    # 见 `tasks/understand_tasks.py` 的 `understand_document_task`）。
    #
    # 所以 worker 需要一条"从 note_id 反查用户语言"的路：`note.user_id → users.locale`。
    # 这是"prompt 跟语言走"这条需求的**前提**，不是可选优化。
    #
    # 默认 `zh-CN`：现有用户的观感一字不变，而新注册用户在
    # `PUT /api/auth/locale` 之前也按中文处理（与 `DEFAULT_LOCALE` 一致）。
    locale: Mapped[str] = mapped_column(
        String(10), default="zh-CN", server_default="zh-CN", nullable=False
    )
