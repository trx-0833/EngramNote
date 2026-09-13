"""
JWT 认证服务模块

本模块提供用户认证的核心业务逻辑，包括密码哈希、JWT Token 生成与验证、
用户注册和登录认证。被 auth.py API 层调用，不直接暴露 HTTP 接口。

主要职责：
- 密码哈希与验证（使用 bcrypt 算法）
- JWT 访问令牌（access）与刷新令牌（refresh）的签发与解码
- 用户注册（邮箱和用户名唯一性校验）
- 用户登录认证

设计决策：
- 直接使用 bcrypt 库而非 passlib，避免 passlib 与 bcrypt 版本兼容性问题
- 访问令牌 payload 只存 user_id（sub）与时间声明，保持轻量、保持无状态
- **令牌类型由 `typ` 声明区分**（阶段 6.3）：刷新令牌不能被当成访问令牌用，
  反之亦然。缺失 `typ` 的令牌按**访问令牌**处理，只为兼容本次改造之前
  签发的存量令牌（它们在 24 小时内必须继续可用）—— 这条兼容规则是本模块
  唯一允许"没有 typ 也算数"的地方，见 decode_access_token。
- 刷新令牌的**服务端状态**（撤销、轮换链）不在这里，而在
  `services/refresh_token_service.py`：本模块只负责 JWT 编解码这类纯函数。
- 注册时分别检查邮箱和用户名唯一性，但返回同一句文案（防用户枚举）
- 登录失败返回 None 而非抛异常，由 API 层统一处理响应

⚠️ 任何日志都**不得**打印完整令牌（阶段 6.3 要求）。需要标识一枚令牌时
一律用 `jti` 前 8 位或 `jti_prefix()`。
"""

from dataclasses import dataclass
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
from .password_policy import validate_password

settings = get_settings()
logger = logging.getLogger(__name__)

#: JWT `typ` 声明取值（阶段 6.3）。用 `typ` 而不是自定义 `token_type`：
#: `typ` 是 JWT 规范（RFC 7519 §4.1.9）里的保留声明名，语义现成，
#: 第三方库/jwt.io 也能直接读懂这枚令牌是什么。
ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"


def jti_prefix(jti: Optional[str], length: int = 8) -> str:
    """
    取 jti 前缀，专供日志/排查使用

    为什么要有这么一个函数：阶段 6.3 的硬要求是**任何日志都不得出现完整令牌**，
    而 `jti` 虽然不是令牌本身，却是"用它能定位到具体哪一行记录"的标识。
    把截断规则收在一个函数里，比在每个日志调用点上各写一遍
    `jti[:8]` 更不容易漏（漏一处的后果是完整标识进日志）。

    Args:
        jti: 完整 jti；None/空串时返回 "-"
        length: 保留的字符数

    Returns:
        str: 截断后的前缀
    """
    return (jti or "-")[:length]



def hash_password(password: str) -> str:
    """
    对密码进行 bcrypt 哈希

    使用 bcrypt 算法生成密码哈希值，自动生成随机盐值。
    直接使用 bcrypt 库而非 passlib，避免 passlib 与 bcrypt 版本兼容性问题。

    ⚠️ cost（轮数）由 `settings.bcrypt_rounds` 决定（阶段 6.1：从库默认值
    改为**显式配置**）。改这个值**不会让旧哈希失效**：bcrypt 把 cost 写在
    哈希串里（`$2b$12$...`），校验时按各自记录的 cost 计算。因此提升 cost
    只影响新密码，存量用户在新设密码时自动升级。

    Args:
        password: 明文密码

    Returns:
        str: bcrypt 哈希后的密码字符串
    """
    salt = bcrypt.gensalt(rounds=settings.bcrypt_rounds)
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    验证明文密码与哈希密码是否匹配

    注意 bcrypt 只取前 72 字节且**静默截断**：中文（3 字节/字）在第 25 个字
    之后的部分实际不参与校验。此处显式截断并记录，避免"以为设了长密码"的误解。

    Args:
        plain_password: 用户输入的明文密码
        hashed_password: 数据库中存储的哈希密码

    Returns:
        bool: 密码匹配返回 True，否则返回 False
    """
    try:
        raw = plain_password.encode("utf-8")[:72]
        return bcrypt.checkpw(raw, hashed_password.encode("utf-8"))
    except Exception as e:
        # 存储的哈希损坏时不应抛 500（会被误读为"服务故障"），按验证失败处理
        logger.warning("密码校验异常（按失败处理）: %s", type(e).__name__)
        return False


# 用于登录时序对齐的固定哈希（cost 与真实哈希一致）。
# 目的：邮箱不存在时也执行一次 bcrypt，使两条路径耗时接近，
# 消除"响应时间可区分邮箱是否注册"的侧信道（见 docs/overhaul-plan.md §2.5 E-2）。
#
# ⚠️ 必须与 `hash_password` 用**同一个 cost**（阶段 6.1 起两者都读
# `settings.bcrypt_rounds`）：这个哈希存在的唯一意义就是耗时对齐，
# cost 不一致会让它重新变成一个可测量的侧信道。
_DUMMY_HASH = bcrypt.hashpw(
    b"engramnote-timing-equalizer", bcrypt.gensalt(rounds=settings.bcrypt_rounds)
).decode("utf-8")


def create_access_token(user_id: str) -> str:
    """
    生成 JWT access token（访问令牌）

    Token payload 中包含：
    - sub: 用户 ID，用于后续认证时识别用户
    - exp: 过期时间，基于配置的 jwt_expire_minutes 计算
    - iat: 签发时间
    - typ: 固定为 "access"，用于与刷新令牌区分（阶段 6.3）

    ⚠️ 访问令牌**仍然是完全无状态**的：它无法被单独吊销，登出后仍在
    `exp` 之前有效。会话级撤销由刷新令牌承担（见 refresh_token_service），
    它保证会话无法被继续延长，但已经签发出去的那一枚访问令牌要等它自然过期。
    这是"不为每个请求增加一次黑名单查表"的取舍，风险与后续方案见
    docs/overhaul-plan.md 附录 BA.3。

    Args:
        user_id: 用户 ID，将作为 Token 的 subject 声明

    Returns:
        str: 编码后的 JWT 字符串
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": user_id,              # subject：Token 主体（用户 ID）
        "exp": expire,               # expiration：过期时间
        "iat": now,                  # issued at：签发时间
        "typ": ACCESS_TOKEN_TYPE,    # type：令牌类型（阶段 6.3）
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def is_access_token_payload(payload: dict) -> bool:
    """
    判断 JWT payload 是否属于访问令牌（类型混淆防线，阶段 6.3）

    ## 为什么缺失 `typ` 也算访问令牌

    这是**唯一**为兼容性让步的地方：本次改造之前签发的令牌没有 `typ`，
    而它们必须在各自 `exp` 之前继续可用（否则正在使用的会话会被升级动作
    直接踢下线）。因此规则是"没有 `typ` ⇒ 按访问令牌处理"。

    反向则**严格**：`typ` 明确写着 `refresh` 的令牌永远不是访问令牌。
    这条规则让"拿刷新令牌去调业务接口"必然 401 —— 刷新令牌有效期 30 天，
    若它能当访问令牌用，等于把整条会话的有效期从 24 小时拉到 30 天。

    判据集中在这里而不是各处 `payload.get("typ") != "refresh"`：
    判据写散之后，新增一处解码点很容易漏掉，而这类漏掉不会报错。

    Args:
        payload: 已通过签名与过期校验的 JWT payload

    Returns:
        bool: 是访问令牌返回 True
    """
    return payload.get("typ", ACCESS_TOKEN_TYPE) == ACCESS_TOKEN_TYPE


def _decode_payload(token: str) -> Optional[dict]:
    """
    解码 JWT 并校验签名与过期时间（访问/刷新令牌共用的底层步骤）

    **刻意不只暴露给本模块**：调用方拿到的是"任何合法签名的令牌"的 payload，
    因此必须在拿到之后自行判断 `typ`（用 `is_access_token_payload` /
    `decode_refresh_token`）。把类型判断留给调用方是有意的 ——
    底层只回答"这串东西是不是我们签发的、过期没有"。

    Args:
        token: JWT 字符串

    Returns:
        Optional[dict]: 校验通过返回 payload，否则返回 None
    """
    try:
        return jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
    except JWTError:
        # Token 无效、过期或签名不匹配（含格式完全不是 JWT 的输入）
        return None


def decode_access_token(token: str) -> Optional[str]:
    """
    解码访问令牌，提取用户 ID

    验证 Token 的签名、过期时间与**类型**，成功则返回用户 ID，失败返回 None。
    API 层根据返回值决定是否返回 401 错误。

    类型校验：`typ == "refresh"` 的刷新令牌**一律拒绝**（返回 None），
    缺失 `typ` 的按访问令牌放行（存量兼容，理由见 is_access_token_payload）。

    Args:
        token: JWT Token 字符串

    Returns:
        Optional[str]: 成功返回用户 ID，Token 无效/过期/类型不符返回 None
    """
    payload = _decode_payload(token)
    if payload is None or not is_access_token_payload(payload):
        return None
    user_id: Optional[str] = payload.get("sub")
    return user_id


@dataclass(frozen=True)
class RefreshClaims:
    """
    刷新令牌里我们真正需要的声明

    为什么用 dataclass 而不是把 payload dict 往上抛：dict 的键名写错不会报错，
    只会让 `claims["fam"]` 变成 KeyError 或 `claims.get("fam")` 变成 None ——
    后者更糟，它会静默地开出一条新链或让撤销落空。字段化的对象在
    `decode_refresh_token` 里一次性校验完毕，调用方拿不到"半个令牌"。
    """

    jti: str
    user_id: str
    family_id: str
    expires_at: datetime


def create_refresh_token(claims: RefreshClaims) -> str:
    """
    生成 JWT refresh token（刷新令牌）

    刷新令牌只用于换取新的令牌对，不能访问任何业务接口
    （`decode_access_token` 会因 `typ` 不符拒绝它）。

    Args:
        claims: jti / 用户 / 轮换链 / 过期时刻（由 refresh_token_service 生成，
            因为它同时要落库，两边必须用同一组值）

    Returns:
        str: 编码后的 JWT 字符串
    """
    payload = {
        "sub": claims.user_id,
        "exp": claims.expires_at,
        "iat": datetime.now(timezone.utc),
        "typ": REFRESH_TOKEN_TYPE,
        "jti": claims.jti,
        # 链标识放在 JWT 里而不是只存库：重放检测要在**解码之后立刻**
        # 知道该撤销哪条链，不必先查一次表再反查 family。
        "fam": claims.family_id,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_refresh_token(token: str) -> Optional[RefreshClaims]:
    """
    解码刷新令牌，返回其声明（阶段 6.3）

    与 `decode_access_token` 相反的方向同样严格：**只有** `typ == "refresh"`
    且四个声明齐全的令牌才算数。访问令牌、缺失 `typ` 的存量令牌、
    以及本项目签发的任何其他 JWT 在这里都返回 None。

    Args:
        token: JWT 字符串

    Returns:
        Optional[RefreshClaims]: 合法返回声明，否则 None
    """
    payload = _decode_payload(token)
    if payload is None or payload.get("typ") != REFRESH_TOKEN_TYPE:
        return None
    jti = payload.get("jti")
    user_id = payload.get("sub")
    family_id = payload.get("fam")
    exp = payload.get("exp")
    if not jti or not user_id or not family_id or exp is None:
        # 声明不全的刷新令牌视为无效：继续往下走会得到"无法撤销的会话"，
        # 而那比"多让用户登一次"糟糕得多。
        return None
    try:
        expires_at = datetime.fromtimestamp(int(exp), tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    return RefreshClaims(
        jti=str(jti), user_id=str(user_id),
        family_id=str(family_id), expires_at=expires_at,
    )



async def register_user(db: AsyncSession, req: UserRegisterRequest) -> User:
    """
    注册新用户

    执行以下步骤：
    1. 检查邮箱是否已被注册
    2. 检查用户名是否已被使用
    3. 创建用户记录（密码经过 bcrypt 哈希）

    安全说明（见 docs/overhaul-plan.md §2.5 E-2）：
    旧实现对邮箱占用与用户名占用返回**不同的具体文案**，等于提供一个
    免费的"该邮箱是否已注册"查询接口（可批量探测用于钓鱼/撞库目标筛选）。
    现统一为同一句文案，调用方无法区分冲突原因。
    代价：用户体验略降（不知道是邮箱还是用户名被占）。这是有意的取舍 ——
    想同时保留体验与隐私，应改为「邮箱验证后才创建账号」的异步流程。

    Args:
        db: 异步数据库会话
        req: 注册请求体，包含 email、username、password

    Returns:
        User: 新创建的用户对象

    Raises:
        ValueError: 邮箱或用户名已被占用（不区分哪一个）
    """
    # 邮箱归一化（小写 + 去空白），避免大小写撞库，见 docs/decisions.md#F-21b
    email = (req.email or "").strip().lower()

    # 密码策略（阶段 6.1）：schema 层已经判过一次，这里再判一次是**防御**
    # 绕过 schema 的调用方（脚本、内部工具、将来的改密接口）。
    # 只约束注册/改密，不追溯既有账号 —— 见 password_policy 的模块说明。
    reason = validate_password(req.password, username=req.username, email=email)
    if reason:
        raise ValueError(reason)

    # 检查邮箱是否已存在
    result = await db.execute(select(User).where(User.email == email))
    email_taken = result.scalars().first() is not None

    # 检查用户名是否已存在
    result = await db.execute(select(User).where(User.username == req.username))
    username_taken = result.scalars().first() is not None

    # 统一文案：不区分邮箱/用户名冲突，避免用户枚举
    if email_taken or username_taken:
        logger.info(
            "注册被拒（凭据已被占用）: email_taken=%s, username_taken=%s",
            email_taken, username_taken,
        )
        raise ValueError("该邮箱或用户名已被使用，请更换后重试")

    # 创建用户，密码经过哈希处理
    user = User(
        email=email,
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
    # 登录邮箱同样归一化，与注册一致，见 docs/decisions.md#F-21b
    email = (email or "").strip().lower()
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalars().first()
    if not user:
        # 时序对齐：邮箱不存在时也跑一次 bcrypt，否则"立即返回"与
        # "跑完 bcrypt 再返回"的耗时差异（~100ms）足以精确枚举已注册邮箱。
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, user.hashed_password):
        return None
    logger.info("用户登录成功: user_id=%s, username=%s", user.id, user.username)
    return user
