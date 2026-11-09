"""设置接口的请求/响应模型

只包含**已经接线**的设置项：学习节奏、提醒、内容与生成。
外观类偏好属于设备本地（localStorage），不经过接口；密码/删号/深色主题
不在本批范围，因此不出现在契约里 —— 避免"界面能改、后端不读"的假开关。
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

OutputLocale = Literal["follow", "zh-CN", "en"]
Retention = Literal[0.8, 0.85, 0.9, 0.95]
MaxInterval = Literal[365, 1825, 3650]


class StudySettings(BaseModel):
    daily_review_limit: int = Field(..., ge=5, le=100)
    desired_retention: float = Field(..., ge=0.70, le=0.98)
    max_interval_days: int = Field(..., ge=365, le=3650)


class ReminderSettings(BaseModel):
    email_enabled: bool
    email_hour: int = Field(..., ge=0, le=23)
    #: 部署侧是否真的具备发信能力（全局开关 + SMTP 配置）。
    #: 为 false 时前端应禁用开关并说明原因，而不是让用户点了没反应。
    email_configured: bool


class ContentSettings(BaseModel):
    output_locale: OutputLocale
    card_max_new_per_run: int = Field(..., ge=50, le=1000)
    clean_simplify_formula: bool
    clean_normalize_spacing: bool
    clean_normalize_heading_levels: bool


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
    content: ContentSettings
    account: AccountSettings
    about: AboutSettings


class StudySettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    daily_review_limit: Optional[int] = Field(default=None, ge=5, le=100)
    desired_retention: Optional[Retention] = None
    max_interval_days: Optional[MaxInterval] = None


class ReminderSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email_enabled: Optional[bool] = None
    email_hour: Optional[int] = Field(default=None, ge=0, le=23)


class ContentSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_locale: Optional[OutputLocale] = None
    card_max_new_per_run: Optional[int] = Field(default=None, ge=50, le=1000)
    clean_simplify_formula: Optional[bool] = None
    clean_normalize_spacing: Optional[bool] = None
    clean_normalize_heading_levels: Optional[bool] = None


class SettingsPatch(BaseModel):
    """`PATCH /api/settings`：每段可选，段内字段也可选

    `extra="forbid"` 是刻意的：拼错字段名必须 422，而不是静默忽略后
    让用户以为保存成功（典型例子：把 `daily_review_limit` 写成
    `daily_limit`）。
    """

    model_config = ConfigDict(extra="forbid")

    study: Optional[StudySettingsPatch] = None
    reminders: Optional[ReminderSettingsPatch] = None
    content: Optional[ContentSettingsPatch] = None


class StorageUsage(BaseModel):
    used_bytes: int = Field(..., ge=0)
    #: 0 = 不限制（与 `max_storage_per_user_mb=0` 的语义一致）
    quota_bytes: int = Field(..., ge=0)


class SettingsUsageResponse(BaseModel):
    storage: StorageUsage
