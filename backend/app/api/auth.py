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
- 认证依赖从请求头 Authorization: Bearer <token> 中提取 Token
- Token 验证失败时返回 401 并设置 WWW-Authenticate 头，符合 HTTP 规范
- 同时检查用户是否存在和是否激活（is_active），禁用用户无法通过认证
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.user import User
from ..schemas.user import (
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)
from ..services.auth_service import (
    authenticate_user,
    create_access_token,
    decode_access_token,
    register_user,
)

router = APIRouter()


async def get_current_user_dependency(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    """
    FastAPI 依赖：从 Authorization 请求头提取并验证当前用户

    该依赖被其他需要认证的接口通过 Depends() 注入使用。
    解析流程：
    1. 从请求头中提取 Bearer Token
    2. 解码 JWT 获取 user_id
    3. 从数据库查询用户并验证是否激活

    Args:
        request: FastAPI 请求对象，用于读取请求头
        db: 异步数据库会话，通过依赖注入获取

    Returns:
        User: 当前认证用户对象

    Raises:
        HTTPException 401: 未提供 Token、Token 无效/过期、用户不存在或已禁用
    """
    # 从请求头中提取 Authorization 字段
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 去掉 "Bearer " 前缀，提取纯 Token 字符串
    token = auth_header[7:]
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
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

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
