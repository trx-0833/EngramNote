"""
认证 API 模块

本模块提供用户认证相关的 HTTP 接口，包括注册、登录和获取当前用户信息。
同时定义了 get_current_user_dependency 依赖，供其他需要认证的接口使用。

主要职责：
- 用户注册接口（POST /api/auth/register）
- 用户登录接口（POST /api/auth/login）
- 获取当前用户信息接口（GET /api/auth/me）
- 提供认证依赖 get_current_user_dependency，从 JWT Token 中提取当前用户

设计决策：
- 注册成功后自动签发 Token，用户无需再次登录
- 认证依赖使用 FastAPI 的 HTTPBearer 安全方案解析
  `Authorization: Bearer <token>`，而非手写字符串切分
- Token 验证失败时返回 401 并设置 WWW-Authenticate 头，符合 HTTP 规范
- 同时检查用户是否存在和是否激活（is_active），禁用用户无法通过认证
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.user import User
from ..schemas.user import (
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserReminderSettingsResponse,
    UserResponse,
)
from ..services.auth_service import (
    authenticate_user,
    create_access_token,
    decode_access_token,
    register_user,
)

router = APIRouter()

# Bearer 认证安全方案
#
# 为什么用 HTTPBearer 而不是手写 `request.headers.get("Authorization")[7:]`：
# 1. 手写解析不会在 OpenAPI 里注册 securitySchemes，于是 /docs 没有
#    Authorize 按钮，openapi.json 也完全不体现接口需要认证 ——
#    前端与第三方无法从契约得知哪些接口要 Token；
# 2. `auto_error=True` 在缺失/格式错误的 Authorization 头时直接抛
#    401 + WWW-Authenticate: Bearer，与下面 token 无效的分支保持一致的契约；
# 3. 字符串切分对 "Bearer" 大小写、多余空格等边界情况没有定义行为。
#
# 注意：HTTPBearer 抛出的 401 由 main.py 的 http_exception_handler 统一渲染，
# 该处理器必须转发 exc.headers，否则这个头依然到不了客户端。
bearer_scheme = HTTPBearer(auto_error=True, description="JWT 访问令牌")


async def get_current_user_dependency(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    FastAPI 依赖：从 Authorization 请求头提取并验证当前用户

    该依赖被其他需要认证的接口通过 Depends() 注入使用。
    解析流程：
    1. 由 HTTPBearer 安全方案提取 Bearer Token（缺失/格式错时 401）
    2. 解码 JWT 获取 user_id
    3. 从数据库查询用户并验证是否激活

    Args:
        credentials: HTTPBearer 解析出的凭证（scheme + credentials）
        db: 异步数据库会话，通过依赖注入获取

    Returns:
        User: 当前认证用户对象

    Raises:
        HTTPException 401: 未提供 Token、Token 无效/过期、用户不存在或已禁用
    """
    token = credentials.credentials
    # 解码 JWT，获取 user_id
    user_id = decode_access_token(token)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或过期的令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 从数据库查询用户
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    # 同时验证用户存在且未被禁用
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已禁用",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(req: UserRegisterRequest, db: AsyncSession = Depends(get_db)):
    """
    用户注册接口

    注册成功后自动签发 JWT Token，用户无需再次登录即可使用系统。
    如果邮箱或用户名已被占用，返回 400 错误。

    Args:
        req: 注册请求体，包含 email、username、password
        db: 异步数据库会话

    Returns:
        TokenResponse: 包含 access_token 和用户信息的响应

    Raises:
        HTTPException 400: 邮箱或用户名已被注册
    """
    try:
        user = await register_user(db, req)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    # 注册成功后自动签发 Token，免去用户再次登录
    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        user=UserResponse.model_validate(user),
    )


@router.post("/login", response_model=TokenResponse)
async def login(req: UserLoginRequest, db: AsyncSession = Depends(get_db)):
    """
    用户登录接口

    使用邮箱和密码进行认证，成功后签发 JWT Token。

    Args:
        req: 登录请求体，包含 email 和 password
        db: 异步数据库会话

    Returns:
        TokenResponse: 包含 access_token 和用户信息的响应

    Raises:
        HTTPException 401: 邮箱或密码错误
    """
    user = await authenticate_user(db, req.email, req.password)
    if not user:
        # 统一返回"邮箱或密码错误"，不区分是邮箱不存在还是密码错误，防止信息泄露
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="邮箱或密码错误",
        )

    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        user=UserResponse.model_validate(user),
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user_dependency)):
    """
    获取当前用户信息接口

    通过认证依赖自动获取当前用户，无需传递用户 ID。
    用于前端获取用户头像、用户名等展示信息。

    Args:
        current_user: 当前认证用户，通过依赖注入获取

    Returns:
        UserResponse: 当前用户信息
    """
    return UserResponse.model_validate(current_user)


@router.get("/reminder-settings", response_model=UserReminderSettingsResponse)
async def get_reminder_settings(
    current_user: User = Depends(get_current_user_dependency),
):
    """
    获取当前用户邮件提醒设置

    通过认证依赖获取当前用户，返回其邮件提醒开关状态。

    Returns:
        UserReminderSettingsResponse: 当前用户的邮件提醒设置
    """
    return UserReminderSettingsResponse.model_validate(current_user)


@router.put("/reminder-settings", response_model=UserReminderSettingsResponse)
async def update_reminder_settings(
    req: UserReminderSettingsResponse,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    """
    更新当前用户邮件提醒设置

    由认证依赖注入的 current_user 仍挂接在会话上，直接改字段并提交即可持久化。

    Returns:
        UserReminderSettingsResponse: 更新后的邮件提醒设置
    """
    current_user.email_reminder_enabled = req.email_reminder_enabled
    await db.commit()
    return UserReminderSettingsResponse.model_validate(current_user)
