"""
LLM 响应缓存模型（overhaul-plan 阶段 4.7）

## 为什么需要它

`chat_detailed` 的输入是**完全确定**的：同一份 messages、同一个模型、
同一组采样参数。而理解管道在下面这些场景里会反复发出**逐字节相同**的请求：

- 重跑理解（阶段 4.8 刚让它变得安全，于是它会变得常见）；
- 同一份资料被再次导入；
- 用户重复点同一个按钮 / 任务重试。

每一次都全额付费，而模型对同样的输入给出的答案在语义上是同一份。

## 与 `llm_calls` 的分工

    llm_calls    一次**调用**的账单（无论钱花没花）
    llm_cache    一份**响应**的存档（按输入寻址）

缓存命中时**仍然写一行 `llm_calls`**（`cached=True`、`cost=0`、
`saved_tokens=N`）。理由是记账表要能回答"这个月本可以花多少"——
只记真实支出的话，缓存带来的节省在任何报表上都**看不见**，
于是"要不要开缓存"永远只能靠感觉决定。

## 为什么 key 是主键、且不按用户分表

`key` 是对**完整输入**（模型 + 端点 + messages + 采样参数）的哈希，
它本身已经包含了全部输入信息。因此：

- 不需要额外的唯一约束，也不需要业务字段索引 —— 查询就是按 key 精确命中；
- 不需要按用户分区：两个用户如果发出**逐字节相同**的请求，
  他们本来就该得到同一份回答（输入相同 ⇒ 输出相同）。
  按用户分表只会让"同一份资料被两个用户导入"时白付两次钱，
  而不带来任何隐私收益（key 里已经含全部输入）。

`response_json` 存的是**完整的成功响应体**，不是一个摘要 ——
缓存命中时调用方要拿到与真实调用等价的返回。
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel, TZDateTime


class LLMCache(BaseModel):
    """
    一份按输入寻址的 LLM 响应

    Attributes:
        key: 输入指纹（sha256），主键
        provider / model: 冗余存一份，便于排查"命中率为什么变了"
        response_json: 完整响应体（JSON 字符串）
        finish_reason: 停止原因（"stop" / "length"）
        prompt_tokens / completion_tokens / total_tokens: 原始用量，
            缓存命中时用它算 `saved_tokens`
        hit_count / last_hit_at: 命中统计 —— 用来回答
            "哪些缓存真的在省钱、哪些只是占地方"
        expires_at: 过期时刻；NULL 表示不过期
    """
    __tablename__ = "llm_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    response_json: Mapped[str] = mapped_column(Text, nullable=False)
    finish_reason: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_hit_at: Mapped[Optional[datetime]] = mapped_column(
        TZDateTime(timezone=True), nullable=True,
    )
    #: 过期时刻。
    #:
    #: 为什么必须有 TTL，而不是"永远可信"：key 里含模型名，但**供应商可以
    #: 在同一个模型名后面悄悄换权重**。那时缓存会继续返回旧模型的话，
    #: 而任何日志都看不出异常。TTL 是这个问题唯一的兜底。
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        TZDateTime(timezone=True), nullable=True,
    )

    __table_args__ = (
        # 清理任务（按过期时间删旧行）与"哪些缓存从没被命中"的排查
        Index("ix_llm_cache_expires_at", "expires_at"),
        Index("ix_llm_cache_hit_count", "hit_count"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<LLMCache {self.key[:12]} {self.model} hits={self.hit_count}>"


__all__ = ["LLMCache"]
