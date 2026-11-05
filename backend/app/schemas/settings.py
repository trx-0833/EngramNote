"""设置接口的请求/响应模型

只包含**已经接线**的设置项：每日复习上限、邮件提醒（开关/时间）。
外观类偏好属于设备本地（localStorage），不经过接口；内容/留存率等 P2 项
还没有接线，因此不出现在契约里 —— 避免"界面能改、后端不读"的假开关。
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class StudySettings(BaseModel):
    daily_review_limit: int = Field(..., ge=5, le=100)


class ReminderSettings(BaseModel):
    email_enabled: bool
    email_hour: int = Field(..., ge=0, le=23)
    #: 部署侧是否真的具备发信能力（全局开关 + SMTP 配置）。
    #: 为 false 时前端应禁用开关并说明原因，而不是让用户点了没反应。
    email_configured: bool


class AccountSettings(BaseModel):
    email: str
    username: str


class AboutSettings(BaseModel):
    app_version: str
    app_env: str


class SettingsResponse(BaseModel):
    """`GET/PATCH /api/settings` 的完整响应"""

    locale: str
    study: StudySettings
    reminders: ReminderSettings
    account: AccountSettings
    about: AboutSettings


class StudySettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    daily_review_limit: Optional[int] = Field(default=None, ge=5, le=100)


class ReminderSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email_enabled: Optional[bool] = None
    email_hour: Optional[int] = Field(default=None, ge=0, le=23)


class SettingsPatch(BaseModel):
    """`PATCH /api/settings`：每段可选，段内字段也可选

    `extra="forbid"` 是刻意的：拼错字段名必须 422，而不是静默忽略后
    让用户以为保存成功（典型例子：把 `daily_review_limit` 写成
    `daily_limit`）。
    """

    model_config = ConfigDict(extra="forbid")

    study: Optional[StudySettingsPatch] = None
    reminders: Optional[ReminderSettingsPatch] = None


class StorageUsage(BaseModel):
    used_bytes: int = Field(..., ge=0)
    #: 0 = 不限制（与 `max_storage_per_user_mb=0` 的语义一致）
    quota_bytes: int = Field(..., ge=0)


class SettingsUsageResponse(BaseModel):
    storage: StorageUsage
