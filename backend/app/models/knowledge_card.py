"""
知识卡片模型模块

本模块定义了知识卡片数据表模型，存储从笔记中提取的结构化知识点。

主要职责：
- 定义卡片类型枚举 CardType（概念、公式、问答对、定义）
- 定义知识卡片表结构，关联用户和笔记

设计决策：
- 使用枚举类型约束 card_type，避免无效值
- content 字段存储 JSON 字符串，支持不同类型卡片的灵活内容结构
- source_text 保留原始出处，方便用户回溯
- metadata_ 字段使用 JSON 类型，映射到数据库列名为 metadata
"""

import enum
from typing import Any, Dict, List, Optional

from sqlalchemy import Enum, String, Text, JSON, ForeignKey, Float
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


class CardType(str, enum.Enum):
    """
    知识卡片类型枚举

    标识知识点的类型，决定卡片的展示和复习方式：
    - concept: 概念类知识点，需要理解记忆
    - formula: 公式类知识点，需要推导练习
    - qa: 问答对，直接以问答形式呈现
    - definition: 定义类知识点，需要精确记忆
    """
    concept = "concept"
    formula = "formula"
    qa = "qa"
    definition = "definition"


class CardCategory(str, enum.Enum):
    """
    知识卡片分类枚举

    标识知识点的来源分类，用于联合分析、盲点检测与拓展生成：
    - regular: 常规知识点
    - blind_spot: 盲点（资料有但笔记未覆盖）
    - extension: 拓展知识点（基于掌握度生成）
    """
    regular = "regular"
    blind_spot = "blind_spot"
    extension = "extension"


class KnowledgeCard(BaseModel):
    """
    知识卡片模型

    对应数据库中的 knowledge_cards 表。
    每条记录代表从笔记中提取的一个结构化知识点。

    Attributes:
        id: UUID 主键（继承自 BaseModel）
        user_id: 所属用户 ID，外键关联 users 表
        note_id: 来源笔记 ID，外键关联 notes 表
        card_type: 知识点类型（概念/公式/问答对/定义）
        title: 知识点标题
        content: 知识点内容（JSON 字符串，支持不同类型的灵活结构）
        summary: 所属章节的摘要
        chapter_title: 所属章节标题
        source_text: 原始出处文本
        metadata_: 扩展元数据（JSON 格式）
        card_category: 卡片分类（常规/盲点/拓展），用于联合分析与拓展生成
        is_key_point: 是否标记为重点
        is_difficulty: 是否标记为难点
        mastery_level: 掌握度（0.0~1.0）
        source_note_ids: 卡片来源的笔记 ID 列表（拓展卡片可关联多个源笔记）
        parent_card_id: 父卡片 ID（拓展卡片的父卡片），外键关联 knowledge_cards 表
        created_at: 创建时间（继承自 BaseModel）
        updated_at: 更新时间（继承自 BaseModel）
    """
    __tablename__ = "knowledge_cards"

    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True, nullable=False)
    note_id: Mapped[str] = mapped_column(String, ForeignKey("notes.id"), index=True, nullable=False)
    card_type: Mapped[CardType] = mapped_column(Enum(CardType), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    chapter_title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    source_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_: Mapped[Optional[Dict[str, Any]]] = mapped_column("metadata", JSON, nullable=True)
    card_category: Mapped[CardCategory] = mapped_column(Enum(CardCategory), default=CardCategory.regular, nullable=False)
    is_key_point: Mapped[bool] = mapped_column(default=False, nullable=False)
    is_difficulty: Mapped[bool] = mapped_column(default=False, nullable=False)
    mastery_level: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    source_note_ids: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True)
    parent_card_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("knowledge_cards.id"), nullable=True, index=True)
