"""用户 Pydantic Schema — 请求/响应数据模型"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

from ..services.password_policy import MIN_PASSWORD_LENGTH, validate_password


# --- 请求模型 ---

# 注册请求
class UserRegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(
        min_length=2,
        max_length=50,
        pattern=r"^[a-zA-Z0-9]+$",
        description="用户名只能包含英文字母和数字",
    )
    #: 下限与**强度**由 `services/password_policy` 统一判定（阶段 6.1）。
    #: `max_length` 留 200 而不是 72：策略要能给出"密码过长，超出部分不会被校验"
    #: 这条**具体**提示，而不是让 pydantic 抛一句长度超限。
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)

    @field_validator("password")
    @classmethod
    def _check_strength(cls, value: str, info) -> str:
        """注册时校验密码强度（阶段 6.1）

        用 `field_validator` 而不是在服务层判：这样它是一条**接口契约**，
        OpenAPI 文档与 422 响应体里都能看到原因（`detail` 里带具体理由）。
        服务层另有一道同样的判断（防御绕过 schema 的调用方）。
        """
        username = (info.data or {}).get("username")
        reason = validate_password(value, username=username)
        if reason:
            raise ValueError(reason)
        return value


# 登录请求
class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str


# --- 响应模型 ---

class UserResponse(BaseModel):
    id: str
    email: str
    username: str
    is_active: bool
    email_reminder_enabled: Optional[bool] = None
    last_reminded_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# 邮件提醒用户级设置响应
class UserReminderSettingsResponse(BaseModel):
    email_reminder_enabled: bool

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
