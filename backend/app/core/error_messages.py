"""
错误文案的双语表
================

## 这张表解决什么

`AppError(code, message, status)` 的 `message` 会作为响应体的 `detail` 返回给用户。
硬编码中文时，英文界面下的错误提示是中文 —— 这就是本表要修的东西。

## 迁移是**增量**的（这是本设计的要点）

`AppError` 的签名**没有改**，`message` 仍然是必填参数。查表发生在
`middleware/error_handler.py`：

```python
detail = resolve_message(exc.code, fallback=exc.message)
```

于是：

| 情况 | 行为 |
|---|---|
| 这个 code **在本表里** | 按当前语言返回对应文案（**表里的值取代** `raise` 时写的那个） |
| 这个 code **不在表里** | 用 `raise` 时写的 `message`（= 现有行为，一字不变） |

所以 90 处 `raise AppError(...)` 可以**分批迁移**：加一条表项就等于翻译了一处，
而没加的照旧。不需要一次性改完 90 处，也不会出现"改了一半、另一半报 KeyError"。

## ⚠️ 表里的值必须与 `raise` 时的中文**逐字相同**

否则迁移一处就等于**悄悄改了现有用户看到的中文提示**。加表项时的做法是：
从调用点把那个字符串复制过来，而不是重新写一遍。

`tests/test_i18n.py` 有一条守卫：对表里每个 code，断言中文值与
"代码里那次 raise 的 message"一致 —— 靠 AST 扫描 `AppError(` 调用点比对。
（阶段 0 表里只有认证相关的几条，扫描范围覆盖全 `app/`。）

## 新增语言时

`SUPPORTED_LOCALES`（`core/i18n.py`）加一种，本表每种 code 加一个键。
`_MISSING` 哨兵用于"这条还没翻"——它在运行期表现为回退到 `raise` 时的文案，
而不是返回一个空串或英文占位符。
"""

from __future__ import annotations

from typing import Dict, Optional

from .i18n import DEFAULT_LOCALE

#: 结构：`{error_code: {locale: 文案}}`
#:
#: 只登记**用户会看到**的错误码。内部错误（`INTERNAL_ERROR`）不在表里 ——
#: 它由中间件硬编码，且文案是"服务器内部错误，请稍后重试"，
#: 那句话本身就该随语言走，所以它**也在表里**（见下）。
ERROR_MESSAGES: Dict[str, Dict[str, str]] = {
    # ---- 认证（i18n 阶段 0 的范围）----
    "AUTH_INVALID_CREDENTIALS": {
        "zh-CN": "邮箱或密码错误",
        "en": "Incorrect email or password",
    },
    # `AUTH_REGISTRATION_REJECTED` 的 detail 是 `str(ValueError)`，
    # 来自 `register_user` 内部的具体原因（"邮箱已注册" / "用户名已被占用"…），
    # 所以**不能**在这里给一条固定文案 —— 那会把具体原因抹平成一句笼统的话。
    # 它的翻译在 `auth_service` 抛出时就该确定，属于阶段 2 的范围。
    #
    # ---- 认证的 HTTPException（不是 AppError，由中间件单独处理）----
    # 这三条在 `api/auth.py` 里用 `HTTPException(detail=...)` 抛，
    # 错误码是中间件合成的 `HTTP_401`。见 `HTTP_STATUS_MESSAGES`。
}

#: `HTTPException` 的文案（它们没有业务错误码，只有状态码）
#:
#: ⚠️ 这张表**不能**按状态码给通用文案：401 在这一处是"无效或过期的令牌"，
#: 在另一处可能是别的意思。所以键是**原文案本身**（中文），
#: 值是对应语言的翻译 —— 这样"同一个中文句子在不同端点出现"会自动共用翻译，
#: 而"同一状态码下不同句子"不会互相覆盖。
HTTP_STATUS_MESSAGES: Dict[str, Dict[str, str]] = {
    "无效或过期的令牌": {
        "zh-CN": "无效或过期的令牌",
        "en": "Invalid or expired token",
    },
    "用户不存在或已禁用": {
        "zh-CN": "用户不存在或已禁用",
        "en": "User does not exist or has been disabled",
    },
    "未提供认证令牌": {
        "zh-CN": "未提供认证令牌",
        "en": "Authentication token is required",
    },
    "刷新令牌无效或已失效，请重新登录": {
        "zh-CN": "刷新令牌无效或已失效，请重新登录",
        "en": "Refresh token is invalid or expired. Please sign in again.",
    },
}

#: 中间件里硬编码的那句 500 文案（不在任何 `AppError` 里，所以单独登记）
INTERNAL_ERROR_MESSAGES: Dict[str, str] = {
    "zh-CN": "服务器内部错误，请稍后重试",
    "en": "Internal server error. Please try again later.",
}

#: 参数校验失败（`ValidationError` → 422）的兜底文案。
#: ⚠️ 现有行为是把 Pydantic 的英文报错**原样**放进 detail（`str(exc)`），
#: 那是**英文**的 —— 所以本表只在"拿不到 str(exc)"时兜底，不参与正常路径。
VALIDATION_ERROR_MESSAGES: Dict[str, str] = {
    "zh-CN": "请求参数校验失败",
    "en": "Request validation failed",
}


def resolve_message(code: str, fallback: str, locale: str = DEFAULT_LOCALE) -> str:
    """
    按错误码 + 语言取文案；表里没有就用 `fallback`

    Args:
        code: 业务错误码（`AppError.code`）
        fallback: `raise` 时写的那句 `message`（= 现有行为）
        locale: 目标语言

    Returns:
        该语言的文案；该 code 未登记、或登记了但没有这个语言的翻译时，
        返回 `fallback`（**不是空串** —— 空串会让用户看到一个没有内容的错误框）
    """
    entry = ERROR_MESSAGES.get(code)
    if not entry:
        return fallback
    return entry.get(locale) or entry.get(DEFAULT_LOCALE) or fallback


def resolve_http_status_message(detail: str, locale: str = DEFAULT_LOCALE) -> str:
    """
    按**中文原文**取 `HTTPException` 的翻译

    为什么键是原文而不是状态码：见 `HTTP_STATUS_MESSAGES` 的说明。
    没有登记时返回原 `detail`（现有行为）。
    """
    entry = HTTP_STATUS_MESSAGES.get(detail)
    if not entry:
        return detail
    return entry.get(locale) or entry.get(DEFAULT_LOCALE) or detail


def internal_error_message(locale: str = DEFAULT_LOCALE) -> str:
    """500 兜底文案"""
    return INTERNAL_ERROR_MESSAGES.get(locale) or INTERNAL_ERROR_MESSAGES[DEFAULT_LOCALE]


def validation_error_message(locale: str = DEFAULT_LOCALE) -> str:
    """422 兜底文案（正常路径用 Pydantic 自己的英文报错，不走这里）"""
    return VALIDATION_ERROR_MESSAGES.get(locale) or VALIDATION_ERROR_MESSAGES[DEFAULT_LOCALE]


def registered_codes() -> list[str]:
    """已登记的错误码清单（供测试断言"表与调用点一致"）"""
    return sorted(ERROR_MESSAGES)


def _lookup_for_test(code: str, locale: str) -> Optional[str]:
    """仅供测试：取某 code 某语言的登记值（未登记返回 None）"""
    return (ERROR_MESSAGES.get(code) or {}).get(locale)
