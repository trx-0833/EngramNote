"""密码策略（overhaul-plan 阶段 6.1）

## 为什么单独成模块

策略是**纯函数**：输入密码（可选带上用户名/邮箱做相似性判断），
输出"拒绝理由或 None"。这样边界可以逐条验证，而接线（注册接口、服务层）
不必各自重复一套判断。

## 判据与理由

| 规则 | 为什么 |
|---|---|
| 长度 ≥ 8 | 6 位是现代硬件下可离线爆破的量级；这一条是性价比最高的一道 |
| UTF-8 **字节** ≤ 72 | bcrypt **静默截断**前 72 字节：中文 3 字节/字，第 25 个字之后的部分**根本不参与校验**。与其让用户以为设了长密码，不如直接拒绝并说明 |
| 不能全是数字 | 纯数字密码是撞库字典的第一梯队 |
| 不能是同一个字符重复 | `aaaaaaaa` 满足长度却毫无强度 |
| 不在常见弱口令表里 | 撞库字典的头部几十个词覆盖了相当比例的弱口令 |
| 不能包含用户名/邮箱前缀 | "用户名+123" 是社工猜测的第一顺位 |
| 不做"必须含大小写+数字+符号"的强制组合 | 强制组合把人推向 `Password1!` 这类**可预测**的形态，而它同时出现在每一本字典里；长度与"不在字典里"才是有效判据（NIST SP 800-63B 亦不再推荐强制组合） |

## 只约束**注册**，不追溯既有账号

策略在注册（与未来改密）时生效。已有账号即使密码很短也能照常登录 ——
把策略追溯应用到存量密码，等于在用户毫无准备时锁死他们的账号，
而这些人往往正是最需要先能登进来改密码的人。
"""

import re
import unicodedata
from typing import Optional

#: 最短长度（字符数）
MIN_PASSWORD_LENGTH = 8

#: bcrypt 的硬上限（**字节**）：超过这个长度的部分会被静默丢弃
BCRYPT_MAX_BYTES = 72

#: 常见弱口令（撞库字典头部）。刻意保持小：它的作用是挡住"最省事的那几个选择"，
#: 而不是做一个完整的字典库（那需要几十万条与持续更新，属于密码强度服务的事）。
WEAK_PASSWORDS = frozenset(
    {
        "123456", "1234567", "12345678", "123456789", "1234567890",
        "password", "password1", "password123", "passw0rd", "p@ssw0rd",
        "qwerty", "qwerty123", "abc123", "111111", "000000", "666666", "888888",
        "iloveyou", "admin", "admin123", "administrator", "root", "toor",
        "letmein", "welcome", "monkey", "dragon", "sunshine", "princess",
        "woaini", "woaini1314", "5201314", "a123456", "123123", "1qaz2wsx",
        "qazwsx", "zxcvbnm", "asdfgh", "engramnote",
    }
)


def _utf8_len(value: str) -> int:
    return len(value.encode("utf-8"))


def validate_password(
    password: str,
    *,
    username: Optional[str] = None,
    email: Optional[str] = None,
) -> Optional[str]:
    """校验密码强度；通过返回 None，否则返回**可直接展示**的中文理由

    Args:
        password: 明文密码
        username: 用户名（用于"密码不能包含用户名"这一条）
        email: 邮箱（取其 @ 之前的部分做同样的判断）
    """
    if password is None:
        return "密码不能为空"

    # 归一化只用于比较（避免用全角数字、兼容字符绕过弱口令判断）
    normalized = unicodedata.normalize("NFKC", password)
    candidate = normalized.strip().lower()

    if len(normalized) < MIN_PASSWORD_LENGTH:
        return f"密码至少需要 {MIN_PASSWORD_LENGTH} 个字符"
    if _utf8_len(normalized) > BCRYPT_MAX_BYTES:
        # ⚠️ 这条不是"太长了不方便"，而是**安全性**：bcrypt 只取前 72 字节，
        # 超出部分不参与校验。不拒绝的话，用户以为的"超长密码"其实只有前 72 字节有效。
        return (
            f"密码过长（超过 {BCRYPT_MAX_BYTES} 字节，中文约 24 个字）："
            "超出部分不会被校验，请缩短"
        )
    if not candidate:
        return "密码不能只包含空白字符"
    if candidate.isdigit():
        return "密码不能全部是数字"
    if len(set(candidate)) == 1:
        return "密码不能是同一个字符的重复"
    if candidate in WEAK_PASSWORDS:
        return "该密码过于常见，请更换"

    # 相似性：包含用户名，或包含**完整邮箱地址**
    #
    # ⚠️ 刻意**不**匹配"邮箱 @ 之前的部分"：那会把大量正常密码误判成弱密码 ——
    # 例如邮箱 `contract@example.com` 配 `ContractPass123!`（本轮实测就被
    # 自己的规则拒了）。局部字符串重合并不等于可被社工猜中，
    # 而误拒的代价是用户被赶去挑一个更难记的密码（往往就是 `xxx123!`）。
    username_token = (username or "").strip().lower()
    if len(username_token) >= 3 and username_token in candidate:
        return "密码不能包含用户名"
    email_token = (email or "").strip().lower()
    if len(email_token) >= 6 and email_token in candidate:
        return "密码不能包含邮箱地址"

    # 连续性字符（12345678 / abcdefgh）已由"弱口令表 + 纯数字"覆盖大部分；
    # 这里再挡一下明显的递增/递减序列
    if re.fullmatch(r"(?:0123456789|9876543210|abcdefghij|jihgfedcba)+", candidate):
        return "密码不能是连续字符序列"

    return None


__all__ = [
    "BCRYPT_MAX_BYTES",
    "MIN_PASSWORD_LENGTH",
    "WEAK_PASSWORDS",
    "validate_password",
]
