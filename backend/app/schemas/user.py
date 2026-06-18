"""用户 Pydantic Schema — 请求/响应数据模型"""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


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
    password: str = Field(min_length=6, max_length=100)


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
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
