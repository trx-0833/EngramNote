"""
用户级设置模型

## 为什么单独一张表

`users` 表只放认证与身份字段。设置项会随功能持续增加（每日上限、提醒时间、
将来的留存率等），混进 `users` 会让每次加一个旋钮都要动认证模型，也会让
"读用户"与"读设置"这两类查询被迫绑在一起。

## 为什么没有行 = 默认值

新用户、以及迁移前的老用户都没有这一行。读取时用环境配置里的默认值补齐，
`GET` **不写库** —— 打开设置页不应该产生一次写操作（SQLite 单写者）。
只有真正改过设置的用户才会拥有这一行。
"""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


class UserSettings(BaseModel):
    """一个用户的设置覆盖值（不存在时全部回落到环境默认值）"""

    __tablename__ = "user_settings"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_settings_user_id"),
        CheckConstraint(
            "daily_review_limit BETWEEN 5 AND 100",
            name="ck_user_settings_daily_review_limit",
        ),
        CheckConstraint(
            "email_hour BETWEEN 0 AND 23",
            name="ck_user_settings_email_hour",
        ),
        CheckConstraint(
            "desired_retention BETWEEN 0.70 AND 0.98",
            name="ck_user_settings_desired_retention",
        ),
        CheckConstraint(
            "max_interval_days BETWEEN 365 AND 3650",
            name="ck_user_settings_max_interval_days",
        ),
        CheckConstraint(
            "output_locale IN ('follow', 'zh-CN', 'en')",
            name="ck_user_settings_output_locale",
        ),
        CheckConstraint(
            "card_max_new_per_run BETWEEN 50 AND 1000",
            name="ck_user_settings_card_max",
        ),
    )

    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # 每日最多结算多少道题。范围在数据库与应用层都挡一次：
    # 它直接参与额度计算，越界会变成"今天一道也做不了"或"限额形同虚设"。
    daily_review_limit: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=10,
        server_default="10",
    )

    # 邮件提醒发送小时（业务时区 Asia/Shanghai，0-23）。
    # 真正决定"发不发"的是 Celery 每小时的扫描任务，不是这个字段本身。
    email_hour: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=9,
        server_default="9",
    )

    # ---- 学习节奏（P2） ----
    # 目标留存率只影响**未来**排期；不做历史重排。
    desired_retention: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.9,
        server_default="0.9",
    )
    # 单次调度允许的最长间隔（天）；1/5/10 年三档。
    max_interval_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3650,
        server_default="3650",
    )

    # ---- 内容与生成（P2） ----
    # `follow` = 跟随 users.locale；其余为显式 AI 输出语言。
    output_locale: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="follow",
        server_default="follow",
    )
    card_max_new_per_run: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=500,
        server_default="500",
    )
    clean_simplify_formula: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
    )
    clean_normalize_spacing: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="1",
    )
    clean_normalize_heading_levels: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="1",
    )
