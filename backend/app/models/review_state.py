"""
复习状态模型模块（overhaul-plan 阶段 3.1）

## 为什么要把调度状态从 QuizItem 里抽出来

现状是**内容与调度混在同一张表**：

    QuizItem { 题目内容, answer, interval, repetition, EF, next_review_at, ... }
               ↑ 内容，重新生成理解时会被替换    ↑ 学习历史，不该被替换

后果（overhaul-plan 症状 D-3 / L-5 / L-2）：

1. **重跑理解就丢学习历史**：`generate_questions` 替换题目时，
   附在题目行上的 `interval`/`repetition`/`next_review_at` 一起没了。
   用户辛苦复习了一个月的进度，一次"重新生成题目"就归零。
2. **没出过题的卡片无法复习**：调度参数只存在于 `quiz_items` 上，
   卡片本身没有"下次该复习"的概念 → 不可能直接复习卡片（阶段 3.12）。
3. **掌握度算不准**：见 `mastery_service` 的说明。

抽取后的分工：

    QuizItem    { id, card_id, question_type, question, answer, options, ... }
                —— 纯内容，可重新生成、可替换
    ReviewState { user_id, item_type, item_id,    ← 'card' | 'quiz'
                  interval_days, repetition, easiness_factor,
                  next_review_at, last_reviewed_at,
                  lapses, state }
                —— 学习状态，与内容解耦；按 card_id 保留即可跨"重生成"

## item_type 为什么必需

同一张卡片既是"卡片"（可整体回忆）又是"题目的宿主"（具体问法）。
两者的记忆强度不同，却共享同一个 `item_id`（card_id 与 quiz_id 都是
UUID 字符串，语义上不可区分）。因此必须显式区分 `item_type`，
唯一约束也要含它。

## 与 quiz_items 旧字段的关系（渐进迁移，可回退）

本阶段**不删除** `quiz_items.interval` 等旧字段，而是：

- 迁移时把旧值**复制**进 `review_states`（`item_type='quiz'`）；
- 新的答题/复习路径**双写**两边；
- 读取（掌握度、卡片复习、到期队列）逐步切到 `review_states`。

这样做的理由是**可回退**：任一步出问题，旧字段仍在，回滚代码即可恢复。
等阶段 3 全部完成、数据稳定后再单独一步删除旧字段。
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel, TZDateTime

#: item_type 取值（用常量而非 Enum 列，便于将来扩展 card/quiz 之外的复习项）
ITEM_TYPE_CARD = "card"
ITEM_TYPE_QUIZ = "quiz"


class ReviewStateKind(str, Enum):
    """
    学习阶段（对齐 FSRS 的 state 概念，便于将来换算法时无需改表）

    - new:        从未复习
    - learning:   正在学习（连续答对次数还很少）
    - review:     已进入长期复习
    - relearning: 答错后重新学习
    """
    new = "new"
    learning = "learning"
    review = "review"
    relearning = "relearning"


class ReviewState(BaseModel):
    """
    复习状态（用户 × 学习项）

    Attributes:
        user_id: 所属用户
        item_type: 'card' | 'quiz'
        item_id: 卡片 ID 或题目 ID
        interval_days: 当前复习间隔（天），SM-2 的 interval
        repetition: 连续成功次数，SM-2 的 repetition
        easiness_factor: 难度系数，SM-2 的 EF
        next_review_at: 下次到期时间；None 表示立即可复习
        last_reviewed_at: 上次复习时间
        lapses: 累计遗忘次数（quality < 3），用于阶段 3.8 的 leech 检测
        state: 学习阶段
        review_count: 累计复习次数
    """
    __tablename__ = "review_states"

    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    item_type: Mapped[str] = mapped_column(String(16), nullable=False)
    item_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    interval_days: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    repetition: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    easiness_factor: Mapped[float] = mapped_column(Float, nullable=False, default=2.5)

    #: 允许 NULL：迁移自 quiz_items 的历史行可能没有 next_review_at，
    #: 语义为"立即可复习"（与旧代码 `next_review_at is None` 的判定一致）
    next_review_at: Mapped[Optional[datetime]] = mapped_column(
        TZDateTime(timezone=True), nullable=True, index=True,
    )
    last_reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        TZDateTime(timezone=True), nullable=True,
    )

    lapses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[ReviewStateKind] = mapped_column(
        SAEnum(ReviewStateKind), nullable=False, default=ReviewStateKind.new,
    )
    review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        # 一个用户对同一个学习项只能有一条状态 —— 这是整个抽取的前提。
        # 缺了它，重复迁移会生成多行，而"当前间隔是多少"会有多个答案。
        UniqueConstraint("user_id", "item_type", "item_id", name="uq_review_state_item"),
        # 到期队列的主查询形状：WHERE user_id=? AND next_review_at <= now
        Index("ix_review_states_due", "user_id", "next_review_at"),
        # 卡片/题目维度的反查（掌握度计算、题目详情页）
        Index("ix_review_states_item", "item_type", "item_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"<ReviewState {self.item_type}:{self.item_id[:8]} "
            f"iv={self.interval_days} rep={self.repetition} ef={self.easiness_factor}>"
        )
