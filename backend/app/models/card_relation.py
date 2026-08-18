"""
卡片关系模型模块

本模块定义了知识卡片之间的关系数据表模型，用于构建知识图谱。

主要职责：
- 定义关系类型枚举 RelationType（相关、前置、后续、对比）
- 定义关系状态枚举 RelationStatus（建议、确认、拒绝）
- 定义卡片关系表结构，关联用户和知识卡片

设计决策：
- 使用枚举类型约束 relation_type 和 status，避免无效值
- similarity_score 仅用于自动建议的关系，用户手动创建时为 None
- 通过 CheckConstraint 确保 card_id_1 != card_id_2，防止自引用
"""

import enum
from typing import Optional

from sqlalchemy import Enum, String, Float, ForeignKey, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


class RelationType(str, enum.Enum):
    """
    卡片关系类型枚举

    标识两张知识卡片之间的关系语义：
    - related: 相关关系，两张卡片内容相关联
    - prerequisite: 前置关系，card_id_1 是 card_id_2 的前置知识
    - subsequent: 后续关系，card_id_1 是 card_id_2 的后续知识
    - contrast: 对比关系，两张卡片内容形成对比
    """
    related = "related"
    prerequisite = "prerequisite"
    subsequent = "subsequent"
    contrast = "contrast"


class RelationStatus(str, enum.Enum):
    """
    卡片关系状态枚举

    标识关系的确认状态：
    - suggested: 自动建议，需要用户确认
    - confirmed: 用户已确认
    - rejected: 用户已拒绝
    """
    suggested = "suggested"
    confirmed = "confirmed"
    rejected = "rejected"


class CardRelation(BaseModel):
    """
    卡片关系模型

    对应数据库中的 card_relations 表。
    每条记录代表两张知识卡片之间的一种关系。

    Attributes:
        id: UUID 主键（继承自 BaseModel）
        user_id: 所属用户 ID，外键关联 users 表
        card_id_1: 关系起点的知识卡片 ID，外键关联 knowledge_cards 表
        card_id_2: 关系终点的知识卡片 ID，外键关联 knowledge_cards 表
        relation_type: 关系类型（相关/前置/后续/对比）
        status: 关系状态（建议/确认/拒绝）
        similarity_score: 相似度分数，仅自动建议的关系有值
        created_at: 创建时间（继承自 BaseModel）
        updated_at: 更新时间（继承自 BaseModel）
    """
    __tablename__ = "card_relations"
    __table_args__ = (
        CheckConstraint("card_id_1 != card_id_2", name="ck_card_relation_no_self_ref"),
    )

    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True, nullable=False)
    # 卡片端可空（悬挂引用策略）：物理删除卡片时数据库自动置 NULL，关系记录永不级联删除，
    # 前端以"[已删除的笔记]"灰色占位呈现
    card_id_1: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("knowledge_cards.id", ondelete="SET NULL"), index=True, nullable=True
    )
    card_id_2: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("knowledge_cards.id", ondelete="SET NULL"), index=True, nullable=True
    )
    relation_type: Mapped[RelationType] = mapped_column(Enum(RelationType), nullable=False)
    status: Mapped[RelationStatus] = mapped_column(Enum(RelationStatus), nullable=False)
    similarity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
