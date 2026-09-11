"""
LLM 调用记账模型（overhaul-plan 阶段 4.2）

## 为什么需要它

改造前，每次 LLM 调用只在**日志**里留一行（`llm_service.py` 的
`logger.info("LLM 响应 | scene=... | prompt_tokens=...")`）。日志能看，
但不能回答任何管理问题：

- 这个月一共花了多少？按场景呢？
- 哪篇笔记的理解最贵？哪个用户消耗最多？
- 加了提示词缓存之后，省下了多少？

这正是 overhaul-plan 症状 **A-12「成本只进日志」**与原则 **P5「成本是一等公民」**
所指的问题。日志是给排障看的，成本要能**聚合**才叫一等公民。

## 为什么 user_id / note_id 不加外键

这是**刻意**的，而且与其他表的惯例相反：

1. 记账记录描述的是"这笔钱已经花出去了"—— 一个**已发生的事实**，
   它不依附于用户或笔记是否还存在。用户注销、笔记删除都不该让
   "我们花过这笔钱"这件事消失。
2. 加外键会带来一个更糟的后果：删除用户时要么被约束挡住（删不掉），
   要么级联删掉账目（账目凭空少了一截）。两者都比"留下一条
   `user_id` 指向已不存在的用户"更糟。
3. 聚合报表按 `user_id` 分组时，孤儿行只会落进"已注销"那一档，
   不会污染任何在册用户的数字。

`note_id` 同理。

## 为什么 cost 允许为 NULL 而不是 0

价格是**配置项**（`llm_price_input_per_1m` / `llm_price_output_per_1m`），
不同供应商、不同时期都不一样。没配置价格时：

    记 0  → 报表上"花费 0 元"，与"免费模型"无法区分
    记 NULL → 报表上"价格未知"，如实

`cost_known` 的判断一律用 `IS NULL`，**不要**用 `= 0`。
"""

from typing import Optional

from sqlalchemy import Boolean, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


class LLMCall(BaseModel):
    """
    一次 LLM 调用（成功或失败）的记账记录

    Attributes:
        user_id: 这次调用为谁而做；NULL = 调用时没有上下文（见
            `llm_accounting_service.llm_context`）
        note_id: 关联的笔记；NULL 同义
        task: 触发这次调用的任务/端点名（如 `understand_note`、`qa_stream`）
        scene: `llm_service` 里传的场景标识（如 `extract_knowledge_points`）
        provider / model: 实际使用的供应商与模型
        prompt_tokens / completion_tokens / total_tokens: token 用量
        cached_tokens: 命中提示词缓存的 token 数（DeepSeek 的
            `prompt_cache_hit_tokens`）—— 它是**省钱的主要杠杆**，
            不记下来就无法回答"上缓存之后省了多少"
        cost / currency: 折算金额；价格为未知时**必须留 NULL**（见模块说明）
        latency_ms: 本次调用耗时（毫秒）
        success: 是否成功；失败也要记账（见类文档下方的说明）
        error: 失败原因（截断到 500 字符）
    """
    __tablename__ = "llm_calls"

    # 均为 String 且**无外键**，理由见模块说明
    user_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    note_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    task: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    scene: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cached_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)

    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    #: 失败也要记账。理由与"predicted_retention 是一扇单向门"同类：
    #: 失败的重试风暴恰恰是最烧钱的形态（`llm_max_retries=5`，
    #: 每次失败都可能已经消耗了 prompt token），只记成功等于把
    #: 最该被看见的那部分成本藏起来。
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    #: 本次是否命中响应缓存（阶段 4.7）
    #:
    #: 命中时**没有花钱**，所以 `total_tokens` / `cost` 都是 0/NULL ——
    #: 这是刻意的：配额（4.3）按 `total_tokens` 求和，命中行若记原始用量，
    #: 会把没花的钱算进配额，于是"开了缓存反而更快被限流"。
    #: 省下来的量记在 `saved_tokens` 里，两者分工明确。
    cached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: 命中缓存省下的 token 数（阶段 4.7）；未命中时为 NULL
    #:
    #: 没有它，"开缓存省了多少钱"在任何报表上都看不见 ——
    #: 而看不见的收益等于没有收益，没人能据此决定要不要继续开。
    saved_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        # 时间范围查询（报表都带 `created_at >= ?`）
        Index("ix_llm_calls_created_at", "created_at"),
        # 报表的主查询形状：WHERE user_id = ? AND created_at >= ?，再 GROUP BY scene
        Index("ix_llm_calls_user_created", "user_id", "created_at"),
        # 全局成本看板（阶段 6）的形状
        Index("ix_llm_calls_created_scene", "created_at", "scene"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"<LLMCall {self.scene} {self.model} "
            f"tokens={self.total_tokens} cost={self.cost} ok={self.success}>"
        )


__all__ = ["LLMCall"]
