"""
刷新令牌模型模块（阶段 6.3：token 可吊销）

## 为什么需要这张表

改造前签发的 JWT 是**完全无状态**的：payload 只有 `sub` + `exp`，
服务端不留任何记录。于是"登出"只是前端删掉 localStorage 里的那一串，
服务端一无所知 —— 令牌在 `jwt_expire_minutes`（默认 1440 分钟）内
**依然有效**，被泄露/被复制的那一份照样能用满 24 小时。

`refresh_tokens` 就是那张"服务端还记得的名单"：

- 每一行 = 一枚**刷新令牌**（不是访问令牌）的签发记录，主键语义由 `jti` 承担；
- `revoked_at` 非空 = 这枚令牌已作废 —— 也就是计划里说的 **jti 黑名单**，
  只不过它是用一张带状态的表表达的，而不是另一张"黑名单表"；
- `replaced_by_jti` 记录轮换链：A 换成 B 时 A 被撤销并指向 B。
  当 A 再次出现（已经被换掉了却还在用），说明**同一枚令牌有两个人持有**，
  即泄露 —— 服务层据此撤销整条 `family_id`。

## family_id 的粒度是"一次登录"，不是"一个用户"

每次登录开一条新链（新 family），轮换沿用同一条链。因此：

- 轮换只作废**本设备当前那一枚**，其他设备完全不受影响；
- 检测到重放时只杀**这一条链**（一次登录），而不是把用户所有设备踢下线 ——
  误判的代价被限制在"当事人重登一次"。

## 为什么可以放心删过期行

过期行对安全没有任何贡献：令牌本身在 `exp` 之后连签名校验都过不去
（`jose` 直接抛 `ExpiredSignatureError`），根本走不到查表这一步。
所以清理任务只删 `expires_at` 已过的行，**不碰**"已撤销但尚未过期"的行 ——
后者正是重放检测要靠的证据（见 `refresh_token_service.purge_expired`）。
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel, TZDateTime


class RefreshToken(BaseModel):
    """
    一枚刷新令牌的签发记录（同时充当"已撤销 jti"的黑名单）

    Attributes:
        id: UUID 主键（继承自 BaseModel；仅用于定位行，不对外暴露）
        jti: JWT 的 `jti` 声明，唯一 —— 令牌与数据库行靠它对应
        user_id: 所属用户，外键关联 users 表，用户被物理删除时级联清理
        family_id: 轮换链标识（一次登录一条链），重放时按它整条撤销
        issued_at: 签发时刻（写入 JWT 的 `iat`，与 `created_at` 同源同时刻；
            显式存一列是为了让"行里的时间"与"令牌里的时间"可直接对照，
            排查时钟/时区类问题时不必再去解码令牌）
        expires_at: 过期时刻（与 JWT 的 `exp` 同一时刻）
        revoked_at: 撤销时刻，NULL 表示仍然有效
        replaced_by_jti: 轮换出的下一枚令牌 jti；NULL 表示不是因轮换而撤销
            （登出撤销、整链撤销都不填）
    """
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        # 重放检测与"整链撤销"都按 family 扫描，单列索引足够
        Index("ix_refresh_tokens_family_id", "family_id"),
    )

    # JWT 的 jti：uuid4().hex（32 字符），留 64 位宽余量
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    # 所属用户；删除用户时其全部令牌记录一并消失
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 轮换链：同一链上的令牌共享它，登录时新开一条
    family_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # 签发/过期时刻（与令牌的 iat/exp 一致）
    issued_at: Mapped[datetime] = mapped_column(TZDateTime(timezone=True), nullable=False)
    # 清理任务按它删行，因此单独建索引
    expires_at: Mapped[datetime] = mapped_column(
        TZDateTime(timezone=True), nullable=False, index=True
    )
    # 撤销时刻（NULL = 有效）
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        TZDateTime(timezone=True), nullable=True
    )
    # 轮换链上的下一枚（NULL = 非轮换撤销）
    replaced_by_jti: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        # ⚠️ 只打 jti 前 8 位：完整令牌标识不应进日志（阶段 6.3 要求"不记录完整令牌"）
        return (
            f"<RefreshToken {self.jti[:8]} user={(self.user_id or '')[:8]} "
            f"revoked={self.revoked_at is not None}>"
        )


__all__ = ["RefreshToken"]
