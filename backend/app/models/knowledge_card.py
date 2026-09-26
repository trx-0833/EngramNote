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

from sqlalchemy import Boolean, Enum, Index, Integer, LargeBinary, String, Text, JSON, ForeignKey, Float
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
    # 来源笔记 ID；可空——物理删除笔记时勾选"提升核心卡片"则置 NULL，卡片成为图谱独立节点
    note_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("notes.id"), index=True, nullable=True)
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
    #: 卡片内容的规范化指纹（阶段 4.8：重跑理解的幂等键）
    #:
    #: ## 为什么必须是**独立成列**，而不是每次重跑时现算
    #:
    #: 重跑理解时要在"这张卡是不是已经有了"上做判定，而判定必须能走索引 ——
    #: 一篇笔记几百张卡，逐行读出 content 现算哈希是 O(n) 的文本比较。
    #: 更重要的是：**指纹一旦写死就不会随内容漂移**，将来若调整规范化规则
    #: （比如是否忽略标点），旧行的指纹仍然记录着"入库当时的身份"，
    #: 不会因为改了一行代码就让全部历史卡片看起来"从没出现过"。
    #:
    #: 允许 NULL：历史行（本列引入前）由迁移回填；回填失败的（理论上不该有）
    #: 保持 NULL，此时该行**不参与**去重判定 —— 宁可插一张重复的卡，
    #: 也不能因为算不出指纹就把新卡静默丢掉。
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    #: 产出这张卡的**提示词版本**（阶段 4.6：溯源）
    #:
    #: ## 为什么值得单独一列
    #:
    #: 改了提示词之后，"新提示词是不是更好"这个问题必须有答案，而答案只能来自
    #: 数据：把卡片按 `prompt_version` 分组，再看各组的复习表现（保持率、
    #: 判分分布、被标记为盲点的比例）。没有这一列时，同一张表里混着不同
    #: 提示词产出的卡片，任何按提示词分组的比较都做不了 —— 只能"凭感觉"
    #: 判断新版好不好。
    #:
    #: ## 为什么允许 NULL，以及历史行不回填
    #:
    #: 本列引入之前入库的卡片**无法知道**当时用的是哪一版提示词（提示词
    #: 文本改过多次，没有任何记录）。填一个"1"会让这些行假装自己是第一版，
    #: 从而污染"第一版的表现"这个统计口径。NULL 的含义是**未知**，
    #: 与 4.2 里 `cost` 为 NULL 而不是 0 是同一条原则。
    prompt_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    #: 卡片嵌入向量（阶段：关系判定提速）
    #:
    #: ## 为什么必须持久化
    #:
    #: 关系建议原本**每次点击都现算**这 200 张卡的向量：实测编码 200 张要 73 秒，
    #: 而 `POST /graph/suggest` 里 Celery 的 `task.get` 超时是 **180 秒**
    #: （`graph_service.auto_suggest_relations`）。全量 1181 张实测要 **275 秒**，
    #: 必超时 —— 也就是说 `MAX_CARDS_FOR_SUGGEST = 200` 这个上限**是被超时逼出来的**，
    #: 而它正是"1061 张卡片一个边都没有"的主因（904 张根本进不了候选池）。
    #:
    #: 存下来之后：余弦矩阵 1181² 实测 **10 ms**、每行 Top-6 **15 ms** —— 与现算的
    #: 275 秒差四个数量级，而且再也不撞超时。
    #:
    #: ## 为什么与 `chunks` 表用**同一套列名与打包方式**
    #:
    #: `app/models/chunk.py:152-157` 已经是这个模式（`embedding_model` /
    #: `embedding_dim` / `embedding` BLOB / `has_embedding`），且 `pack_vector` /
    #: `unpack_vector` 就在那里。AGENTS.md §3 的口径是「向量与原文**同库同表**存储，
    #: 不用独立的向量库」—— 这里照抄，不另立一套。
    #:
    #: ## 为什么允许 NULL、且不回填
    #:
    #: 本列引入之前入库的卡片没有向量，**且算不出来**（那需要跑一遍嵌入模型）。
    #: 迁移只加列、不填值；补向量由 worker 任务异步做（一次性，全量约 275 秒）。
    #: `has_embedding = False` 的行**不参与**关系建议 —— 宁可少建议几条边，
    #: 也不能拿一个"缺失"当"相似度 0"去参与排序。
    embedding_model: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    embedding_dim: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    embedding: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    #: 冗余布尔标记：关系建议要按用户过滤 + 只取已嵌入的行，
    #: 避免每次都对 BLOB 列做 `IS NOT NULL` 判断（与 `chunks.has_embedding` 同理）
    has_embedding: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )

    __table_args__ = (
        # 关系建议的取数路径：按用户过滤 + 只取已嵌入的行
        Index("ix_knowledge_cards_user_embed", "user_id", "has_embedding"),
    )
