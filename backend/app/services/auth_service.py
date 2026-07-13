"""
JWT 认证服务模块

本模块提供用户认证的核心业务逻辑，包括密码哈希、JWT Token 生成与验证、
用户注册和登录认证。被 auth.py API 层调用，不直接暴露 HTTP 接口。

主要职责：
- 密码哈希与验证（使用 bcrypt 算法）
- JWT Token 生成与解码
- 用户注册（邮箱和用户名唯一性校验）
- 用户登录认证

设计决策：
- 直接使用 bcrypt 库而非 passlib，避免 passlib 与 bcrypt 版本兼容性问题
- JWT payload 中仅存储 user_id（sub）和过期时间（exp），保持 Token 轻量
- 注册时分别检查邮箱和用户名唯一性，返回具体错误信息
- 登录失败返回 None 而非抛异常，由 API 层统一处理响应
"""

from typing import Optional

import logging
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models.user import User
from ..schemas.user import UserRegisterRequest

settings = get_settings()
logger = logging.getLogger(__name__)


def hash_password(password: str) -> str:
    """
    对密码进行 bcrypt 哈希

    使用 bcrypt 算法生成密码哈希值，自动生成随机盐值。
    直接使用 bcrypt 库而非 passlib，避免 passlib 与 bcrypt 版本兼容性问题。

    Args:
        password: 明文密码

    Returns:
        str: bcrypt 哈希后的密码字符串
    """
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    验证明文密码与哈希密码是否匹配

    Args:
        plain_password: 用户输入的明文密码
        hashed_password: 数据库中存储的哈希密码

    Returns:
        bool: 密码匹配返回 True，否则返回 False
    """
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_access_token(user_id: str) -> str:
    """
    生成 JWT access token

    Token payload 中包含：
    - sub: 用户 ID，用于后续认证时识别用户
    - exp: 过期时间，基于配置的 jwt_expire_minutes 计算

    Args:
        user_id: 用户 ID，将作为 Token 的 subject 声明

    Returns:
        str: 编码后的 JWT 字符串
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": user_id,     # subject：Token 主体（用户 ID）
        "exp": expire,      # expiration：过期时间
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> Optional[str]:
    """
    解码 JWT Token，提取用户 ID

    验证 Token 的签名和过期时间，成功则返回用户 ID，失败返回 None。
    API 层根据返回值决定是否返回 401 错误。

    Args:
        token: JWT Token 字符串

    Returns:
        Optional[str]: 成功返回用户 ID，Token 无效或过期返回 None
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        user_id: Optional[str] = payload.get("sub")
        return user_id
    except JWTError:
        # Token 无效、过期或签名不匹配
        return None


async def register_user(db: AsyncSession, req: UserRegisterRequest) -> User:
    """
    注册新用户

    执行以下步骤：
    1. 检查邮箱是否已被注册
    2. 检查用户名是否已被使用
    3. 创建用户记录（密码经过 bcrypt 哈希）

    Args:
        db: 异步数据库会话
        req: 注册请求体，包含 email、username、password

    Returns:
        User: 新创建的用户对象

    Raises:
        ValueError: 邮箱或用户名已被占用
    """
    # 检查邮箱是否已存在
    result = await db.execute(select(User).where(User.email == req.email))
    if result.scalars().first():
        raise ValueError("该邮箱已被注册")

    # 检查用户名是否已存在
    result = await db.execute(select(User).where(User.username == req.username))
    if result.scalars().first():
        raise ValueError("该用户名已被使用")

    # 创建用户，密码经过哈希处理
    user = User(
        email=req.email,
        username=req.username,
        hashed_password=hash_password(req.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    logger.info("用户注册成功: user_id=%s, username=%s", user.id, user.username)
    return user


async def authenticate_user(db: AsyncSession, email: str, password: str) -> Optional[User]:
    """
    验证用户登录凭据

    根据邮箱查找用户，然后验证密码是否匹配。
    登录失败（邮箱不存在或密码错误）统一返回 None，
    由 API 层返回通用的"邮箱或密码错误"提示，防止信息泄露。

    Args:
        db: 异步数据库会话
        email: 用户邮箱
        password: 明文密码

    Returns:
        Optional[User]: 认证成功返回用户对象，失败返回 None
    """
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalars().first()
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    logger.info("用户登录成功: user_id=%s, username=%s", user.id, user.username)
    return user
