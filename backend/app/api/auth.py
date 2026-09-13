"""
认证 API 模块

本模块提供用户认证相关的 HTTP 接口，包括注册、登录、刷新、登出和获取当前用户信息。
同时定义了 get_current_user_dependency 依赖，供其他需要认证的接口使用。

主要职责：
- 用户注册接口（POST /api/auth/register）
- 用户登录接口（POST /api/auth/login）
- 刷新令牌接口（POST /api/auth/refresh）—— 轮换，阶段 6.3
- 登出接口（POST /api/auth/logout）—— 撤销，阶段 6.3
- 获取当前用户信息接口（GET /api/auth/me）
- 提供认证依赖 get_current_user_dependency，从 JWT Token 中提取当前用户

设计决策：
- 注册/登录成功后签发**一对**令牌（access + refresh），用户无需再次登录
- 认证依赖使用 FastAPI 的 HTTPBearer 安全方案解析
  `Authorization: Bearer <token>`，而非手写字符串切分
- Token 验证失败时返回 401 并设置 WWW-Authenticate 头，符合 HTTP 规范
- 同时检查用户是否存在和是否激活（is_active），禁用用户无法通过认证
- **刷新与登出不走 Bearer 认证依赖**：它们的凭证是请求体里的刷新令牌。
  理由见 /refresh 与 /logout 的文档字符串 —— 若要求先有有效访问令牌，
  这两个接口恰好会在最需要它们的时刻（访问令牌已过期）不可用。
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.user import User
from ..schemas.user import (
    LogoutRequest,
    LogoutResponse,
    RefreshRequest,
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
from ..services.refresh_token_service import (
    issue_refresh_token,
    revoke_refresh_token,
    rotate_refresh_token,
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

    注册成功后自动签发访问令牌与刷新令牌，用户无需再次登录即可使用系统。
    如果邮箱或用户名已被占用，返回 400 错误。

    Args:
        req: 注册请求体，包含 email、username、password
        db: 异步数据库会话

    Returns:
        TokenResponse: 包含 access_token、refresh_token 和用户信息的响应

    Raises:
        HTTPException 400: 邮箱或用户名已被注册
    """
    try:
        user = await register_user(db, req)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    # 注册成功后自动签发令牌对，免去用户再次登录
    token = create_access_token(user.id)
    refresh_token, _ = await issue_refresh_token(db, user.id)
    await db.commit()
    return TokenResponse(
        access_token=token,
        refresh_token=refresh_token,
        user=UserResponse.model_validate(user),
    )


@router.post("/login", response_model=TokenResponse)
async def login(req: UserLoginRequest, db: AsyncSession = Depends(get_db)):
    """
    用户登录接口

    使用邮箱和密码进行认证，成功后签发一对令牌（访问 + 刷新）。
    每次登录开一条**新的轮换链**：因此"在另一台设备登录"不会影响本设备，
    "在另一台设备登出"也不会影响本设备（除非显式要求 all_devices）。

    Args:
        req: 登录请求体，包含 email 和 password
        db: 异步数据库会话

    Returns:
        TokenResponse: 包含 access_token、refresh_token 和用户信息的响应

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
    refresh_token, _ = await issue_refresh_token(db, user.id)
    await db.commit()
    return TokenResponse(
        access_token=token,
        refresh_token=refresh_token,
        user=UserResponse.model_validate(user),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(req: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """
    刷新令牌接口（阶段 6.3）：轮换并返回新的一对令牌

    请求体携带刷新令牌（不是 `Authorization` 头）：

    - 用 `Authorization` 头会把两种令牌混在同一个位置，而"哪个头能放哪种令牌"
      正是类型混淆最容易发生的地方；放在 body 里，接口契约（OpenAPI）也明确
      显示本接口不需要 Bearer 认证，而是由 body 里的刷新令牌自证身份。
    - 本接口**刻意不要求**有效的访问令牌：访问令牌过期正是来刷新的主要场景。

    成功时：签发新的访问令牌 + 新的刷新令牌，并把提交的那一枚标记为
    "已撤销 + replaced_by_jti=新令牌"。客户端**必须**用新刷新令牌替换旧的。

    失败一律 401（不区分原因，避免把"这枚令牌存在过"变成可探测信息）：
    类型不符 / 签名错 / 已过期 / 查无此 jti / **重放已撤销的令牌**。
    最后一种情况服务端会撤销整条轮换链（盗用信号，见 refresh_token_service）。

    Args:
        req: 含 refresh_token 的请求体
        db: 异步数据库会话

    Returns:
        TokenResponse: 新的 access_token / refresh_token 与用户信息

    Raises:
        HTTPException 401: 刷新令牌无效、已过期或已被撤销（重放）
    """
    rotated = await rotate_refresh_token(db, req.refresh_token)
    if rotated is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="刷新令牌无效或已失效，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenResponse(
        access_token=rotated.access_token,
        refresh_token=rotated.refresh_token,
        user=UserResponse.model_validate(rotated.user),
    )


@router.post("/logout", response_model=LogoutResponse)
async def logout(req: Optional[LogoutRequest] = None, db: AsyncSession = Depends(get_db)):
    """
    登出接口（阶段 6.3）：撤销刷新令牌

    ## 为什么这个接口不需要认证

    它的凭证是请求体里的刷新令牌本身，且**允许它已经无效**：

    1. 登出的目的是"让服务端忘掉这个会话"。如果要求先出示有效访问令牌，
       那么访问令牌一旦过期（用户离开一天后回来点登出）就再也清不掉
       服务端的会话状态 —— 撤销能力恰好在你最需要它的时候失效；
    2. 服务端只根据**签名可验证**的令牌里的 jti/sub 动手，不接受客户端传
       user_id。因此"免认证"不等于"任何人能注销别人的会话"：
       没有那枚令牌就撤销不了任何东西（返回 `revoked=0`）；
    3. 接口是幂等的：重复登出、拿已撤销/已过期的令牌登出都返回 200。
       请求体本身也是可选的（`req=None` 等价于空体），
       因为"清不干净"比"校验得严"在这里危险得多。

    代价与缓解：这是一个"未认证即可写库"的端点，因此挂了限流规则
    （见 middleware/rate_limit.py 的 `logout` 规则）。

    ## 撤销范围

    - 默认只撤销提交的这一枚（本设备）；其他设备各有自己的链，不受影响；
    - `all_devices=true` 撤销该用户全部刷新令牌（"退出所有设备"）。
      各设备下一次刷新会 401 → 前端清本地状态回登录页；
    - ⚠️ **已签发的访问令牌在各自 `exp` 之前仍然有效**（无状态令牌的固有性质，
      见 services/refresh_token_service.py 模块说明 §1）。本接口保证的是
      会话无法再被延长。

    Args:
        req: 可选的 refresh_token 与 all_devices 开关；缺省（无请求体）视为空请求
        db: 异步数据库会话

    Returns:
        LogoutResponse: `revoked` = 实际撤销的行数（0 = 无需清理或令牌不可验证）
    """
    body = req or LogoutRequest()
    revoked = await revoke_refresh_token(
        db, body.refresh_token, all_devices=body.all_devices
    )
    return LogoutResponse(revoked=revoked)


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
