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
    """登录/注册/刷新成功后的令牌对

    `refresh_token` 是阶段 6.3 新增的：访问令牌仍然无状态，会话的
    **可撤销性**由这枚刷新令牌承担（服务端有对应记录，可被轮换与撤销）。
    两个字段一起下发，客户端必须两个都存 —— 只存访问令牌等于放弃了撤销能力。
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse


class RefreshRequest(BaseModel):
    """刷新令牌换新令牌对的请求体"""

    refresh_token: str = Field(min_length=1, description="登录/上次刷新时下发的刷新令牌")


class LogoutRequest(BaseModel):
    """登出请求体（阶段 6.3）

    两个字段都**可选**：登出必须在"客户端状态不完整"时也能调用
    （见 `refresh_token_service.revoke_refresh_token` 的说明）。

    - `refresh_token` 为空时服务端没有可撤销的目标，直接返回 `revoked=0`；
    - `all_devices=True` 时撤销该用户全部刷新令牌（各设备都需要重新登录）。
    """

    refresh_token: Optional[str] = None
    #: 是否撤销该用户的**全部**刷新令牌（默认只撤销当前这一枚）
    all_devices: bool = False


class LogoutResponse(BaseModel):
    """登出结果

    返回实际撤销的行数而不是空响应：这样"登出到底有没有清掉服务端状态"
    是可观测的（0 表示这枚令牌此前已经无效/已撤销），
    而不是一个永远成功、什么也不说明的 204。
    """

    revoked: int
