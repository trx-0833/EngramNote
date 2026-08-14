"""
知识图谱服务模块

本模块提供知识图谱的核心业务逻辑，包括图数据查询、
自动建议关系、确认/拒绝/创建/删除关系等功能。

主要职责：
- 获取用户的完整图谱数据（节点 + 边）
- 获取自动建议的关系列表
- 基于嵌入向量相似度自动建议卡片关系
- 确认、拒绝、手动创建和删除关系

设计决策：
- 自动建议使用卡片「标题 + 截断内容」的嵌入向量计算两两相似度
- 相似度阈值 0.75，平衡精度和召回率
- 排除同一笔记内的卡片对，避免章节内高度相似造成噪音建议
- 单次最多处理 200 张卡片，避免计算量过大
- 手动创建的关系直接为 confirmed 状态
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

import numpy as np
from sqlalchemy import and_, case, distinct, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.card_relation import CardRelation, RelationType, RelationStatus
from ..models.knowledge_card import KnowledgeCard
from ..schemas.graph import GraphData, GraphNode, GraphEdge, SuggestedRelation
from ..services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)

# 自动建议的相似度阈值
SIMILARITY_THRESHOLD = 0.75
# 单次最多处理的卡片数量
MAX_CARDS_FOR_SUGGEST = 200
# 用于嵌入计算的卡片内容最大长度（字符），避免超长文本稀释相似度
CONTENT_EMBED_LIMIT = 200


def _build_card_embedding_text(title: str, content: str) -> str:
    """
    构建用于相似度计算的卡片文本（标题 + 截断内容）

    仅用标题信息量不足，容易把同笔记内、标题雷同的卡片误判为相关；
    拼接截断后的内容可显著提升相似度的语义质量。内容为 JSON 字符串，
    截断到 CONTENT_EMBED_LIMIT 字符以控制计算成本。

    Args:
        title: 卡片标题
        content: 卡片内容（JSON 字符串）

    Returns:
        str: 组合后的嵌入文本
    """
    if content:
        return f"{title}\n{content.strip()[:CONTENT_EMBED_LIMIT]}"
    return title


def _compute_pairwise_similarity(vectors: List[List[float]]) -> np.ndarray:
    """
    使用 numpy 向量化计算两两余弦相似度矩阵

    Args:
        vectors: 向量列表

    Returns:
        np.ndarray: n×n 相似度矩阵
    """
    mat = np.array(vectors, dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)  # 避免除零
    normalized = mat / norms
    sim_matrix = normalized @ normalized.T
    return sim_matrix


async def get_graph_data(user_id: str, db: AsyncSession) -> GraphData:
    """
    获取用户的完整知识图谱数据

    查询用户的所有知识卡片作为节点，
    查询 confirmed 和 suggested 状态的关系作为边，
    构建完整的图谱数据结构。

    Args:
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        GraphData: 包含节点列表和边列表的图谱数据
    """
    # 查询用户所有知识卡片，仅取需要的列（避免加载大体积 content 字段）
    cards_result = await db.execute(
        select(
            KnowledgeCard.id,
            KnowledgeCard.title,
            KnowledgeCard.card_type,
            KnowledgeCard.note_id,
        ).where(KnowledgeCard.user_id == user_id)
    )
    cards = list(cards_result.all())

    # 查询用户 confirmed 和 suggested 状态的关系，仅取需要的列
    relations_result = await db.execute(
        select(
            CardRelation.id,
            CardRelation.card_id_1,
            CardRelation.card_id_2,
            CardRelation.relation_type,
            CardRelation.status,
            CardRelation.similarity_score,
        ).where(
            CardRelation.user_id == user_id,
            CardRelation.status.in_([RelationStatus.confirmed, RelationStatus.suggested]),
        )
    )
    relations = list(relations_result.all())

    # 统计每张卡片的关联数：按「无向卡片对」去重（同一对卡片即使存在多条关系也只计 1）
    neighbor_sets: Dict[str, set] = {}
    for rel in relations:
        if rel.status == RelationStatus.confirmed:
            neighbor_sets.setdefault(rel.card_id_1, set()).add(rel.card_id_2)
            neighbor_sets.setdefault(rel.card_id_2, set()).add(rel.card_id_1)

    # 构建节点
    nodes = [
        GraphNode(
            id=card.id,
            title=card.title,
            card_type=card.card_type,
            note_id=card.note_id,
            relation_count=len(neighbor_sets.get(card.id, set())),
        )
        for card in cards
    ]

    # 构建边
    edges = [
        GraphEdge(
            id=rel.id,
            source=rel.card_id_1,
            target=rel.card_id_2,
            relation_type=rel.relation_type,
            status=rel.status,
            similarity_score=rel.similarity_score,
        )
        for rel in relations
    ]

    return GraphData(nodes=nodes, edges=edges)


async def get_suggested_relations(
    user_id: str,
    db: AsyncSession,
) -> List[SuggestedRelation]:
    """
    获取用户的自动建议关系列表

    查询 status=suggested 的关系，并关联知识卡片获取标题。

    Args:
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        List[SuggestedRelation]: 建议关系列表
    """
    # 查询 suggested 状态的关系，仅取需要的列
    relations_result = await db.execute(
        select(
            CardRelation.id,
            CardRelation.card_id_1,
            CardRelation.card_id_2,
            CardRelation.similarity_score,
        ).where(
            CardRelation.user_id == user_id,
            CardRelation.status == RelationStatus.suggested,
        )
    )
    relations = list(relations_result.all())

    if not relations:
        return []

    # 收集所有涉及的卡片 ID
    card_ids = set()
    for rel in relations:
        card_ids.add(rel.card_id_1)
        card_ids.add(rel.card_id_2)

    # 批量查询卡片标题
    cards_result = await db.execute(
        select(KnowledgeCard.id, KnowledgeCard.title).where(
            KnowledgeCard.id.in_(card_ids)
        )
    )
    card_title_map = {row.id: row.title for row in cards_result.all()}

    # 构建建议关系列表
    suggested = []
    for rel in relations:
        suggested.append(SuggestedRelation(
            id=rel.id,
            card_id_1=rel.card_id_1,
            card_id_2=rel.card_id_2,
            card_1_title=card_title_map.get(rel.card_id_1, "(未知)"),
            card_2_title=card_title_map.get(rel.card_id_2, "(未知)"),
            similarity_score=rel.similarity_score,
        ))

    return suggested


async def auto_suggest_relations(user_id: str, db: AsyncSession) -> int:
    """
    基于嵌入向量相似度自动建议卡片关系

    流程：
    1. 获取用户所有知识卡片（最多 200 张）
    2. 使用 EmbeddingService 编码「标题 + 截断内容」
    3. 计算两两相似度
    4. 相似度超过阈值（0.75）、不同笔记且不存在已有关系的，创建 suggested 状态的关系

    Args:
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        int: 新创建的建议关系数量
    """
    # 获取用户所有知识卡片，仅取所需列（含内容用于语义嵌入），限制数量
    cards_result = await db.execute(
        select(
            KnowledgeCard.id,
            KnowledgeCard.title,
            KnowledgeCard.content,
            KnowledgeCard.note_id,
        ).where(KnowledgeCard.user_id == user_id).limit(MAX_CARDS_FOR_SUGGEST)
    )
    cards = list(cards_result.all())

    if len(cards) < 2:
        return 0

    # 编码「标题 + 截断内容」（CPU 密集，放入线程池避免阻塞事件循环）
    # 嵌入模型不可用（如内存不足）时降级：本次不生成建议，避免接口 500
    embedding_service = EmbeddingService()
    texts = [_build_card_embedding_text(card.title, card.content) for card in cards]
    loop = asyncio.get_event_loop()
    try:
        vectors = await loop.run_in_executor(None, embedding_service.encode, texts)
    except Exception as e:
        logger.warning(
            "卡片嵌入生成失败，跳过本次关系建议: user=%s, err=%s",
            user_id, e,
        )
        return 0

    if not vectors:
        return 0

    # 查询已有的所有关系（含 rejected），避免重复建议
    existing_result = await db.execute(
        select(CardRelation).where(CardRelation.user_id == user_id)
    )
    existing_relations = list(existing_result.scalars().all())

    # 构建已有关系集合：(min_id, max_id) -> relation
    existing_pairs: Dict[tuple, CardRelation] = {}
    for rel in existing_relations:
        pair_key = tuple(sorted([rel.card_id_1, rel.card_id_2]))
        existing_pairs[pair_key] = rel

    # 卡片所属笔记映射：同一笔记内的卡片高度相似但缺乏跨知识点价值，跳过
    note_of = {card.id: card.note_id for card in cards}

    # 使用 numpy 向量化计算相似度矩阵（CPU 密集，放入线程池）
    sim_matrix = await loop.run_in_executor(None, _compute_pairwise_similarity, vectors)

    # 遍历上三角，创建建议
    new_count = 0
    new_relations = []

    for i in range(len(cards)):
        for j in range(i + 1, len(cards)):
            similarity = float(sim_matrix[i][j])

            if similarity >= SIMILARITY_THRESHOLD:
                card_id_1 = cards[i].id
                card_id_2 = cards[j].id

                # 排除同一笔记内的卡片对，避免章节内高度相似造成噪音建议
                if note_of.get(card_id_1) == note_of.get(card_id_2):
                    continue

                pair_key = tuple(sorted([card_id_1, card_id_2]))

                # 如果已有关系，跳过
                if pair_key in existing_pairs:
                    continue

                # 无向关系统一排序存储，确保 card_id_1 <= card_id_2
                stored_id_1, stored_id_2 = card_id_1, card_id_2
                if card_id_1 > card_id_2:
                    stored_id_1, stored_id_2 = card_id_2, card_id_1

                relation = CardRelation(
                    user_id=user_id,
                    card_id_1=stored_id_1,
                    card_id_2=stored_id_2,
                    relation_type=RelationType.related,
                    status=RelationStatus.suggested,
                    similarity_score=round(similarity, 4),
                )
                new_relations.append(relation)
                existing_pairs[pair_key] = relation  # 防止同一对重复创建
                new_count += 1

    # 批量添加
    for rel in new_relations:
        db.add(rel)

    if new_count > 0:
        await db.commit()
        logger.info(f"自动建议关系: user={user_id[:8]}, 新增 {new_count} 条建议")

    return new_count


async def confirm_relation(
    relation_id: str,
    user_id: str,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    确认一条建议关系

    将 status 从 suggested 更新为 confirmed。

    Args:
        relation_id: 关系 ID
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        Dict: 操作结果
    """
    result = await db.execute(
        select(CardRelation).where(
            CardRelation.id == relation_id,
            CardRelation.user_id == user_id,
        )
    )
    relation = result.scalars().first()

    if not relation:
        return {"success": False, "error": "关系不存在"}

    if relation.status != RelationStatus.suggested:
        return {"success": False, "error": "该关系不是建议状态，无法确认"}

    relation.status = RelationStatus.confirmed
    await db.commit()

    logger.info(f"确认关系: user={user_id[:8]}, relation={relation_id[:8]}")
    return {"success": True, "relation_id": relation_id}


async def reject_relation(
    relation_id: str,
    user_id: str,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    拒绝一条建议关系

    将 status 从 suggested 更新为 rejected。

    Args:
        relation_id: 关系 ID
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        Dict: 操作结果
    """
    result = await db.execute(
        select(CardRelation).where(
            CardRelation.id == relation_id,
            CardRelation.user_id == user_id,
        )
    )
    relation = result.scalars().first()

    if not relation:
        return {"success": False, "error": "关系不存在"}

    if relation.status != RelationStatus.suggested:
        return {"success": False, "error": "该关系不是建议状态，无法拒绝"}

    relation.status = RelationStatus.rejected
    await db.commit()

    logger.info(f"拒绝关系: user={user_id[:8]}, relation={relation_id[:8]}")
    return {"success": True, "relation_id": relation_id}


async def create_relation(
    card_id_1: str,
    card_id_2: str,
    relation_type: str,
    user_id: str,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    手动创建一条卡片关系

    验证两张卡片都属于该用户，且不存在重复关系后，
    创建 confirmed 状态的关系。

    Args:
        card_id_1: 卡片 1 ID
        card_id_2: 卡片 2 ID
        relation_type: 关系类型字符串
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        Dict: 操作结果，成功时包含 relation_id
    """
    # 验证卡片归属
    cards_result = await db.execute(
        select(KnowledgeCard).where(
            KnowledgeCard.id.in_([card_id_1, card_id_2]),
            KnowledgeCard.user_id == user_id,
        )
    )
    cards = list(cards_result.scalars().all())

    if len(cards) != 2:
        return {"success": False, "error": "卡片不存在或不属于当前用户"}

    # 验证关系类型
    try:
        rel_type = RelationType(relation_type)
    except ValueError:
        return {"success": False, "error": f"无效的关系类型: {relation_type}"}

    # 无向关系统一排序存储，确保 card_id_1 <= card_id_2
    stored_id_1, stored_id_2 = card_id_1, card_id_2
    if rel_type in (RelationType.related, RelationType.contrast):
        if card_id_1 > card_id_2:
            stored_id_1, stored_id_2 = card_id_2, card_id_1

    # 检查重复关系（排除 rejected 状态，允许用户重新创建被拒绝的关系）
    pair_key = tuple(sorted([card_id_1, card_id_2]))
    existing_result = await db.execute(
        select(CardRelation).where(
            CardRelation.user_id == user_id,
            or_(
                and_(CardRelation.card_id_1 == pair_key[0], CardRelation.card_id_2 == pair_key[1]),
                and_(CardRelation.card_id_1 == pair_key[1], CardRelation.card_id_2 == pair_key[0]),
            ),
        )
    )
    existing = existing_result.scalars().first()

    if existing:
        if existing.status == RelationStatus.rejected:
            # 复用已拒绝的记录，更新为 confirmed
            existing.status = RelationStatus.confirmed
            existing.relation_type = rel_type
            existing.similarity_score = None
            existing.card_id_1 = stored_id_1
            existing.card_id_2 = stored_id_2
            await db.commit()
            await db.refresh(existing)
            logger.info(
                f"重新创建关系(复用rejected): user={user_id[:8]}, "
                f"card1={stored_id_1[:8]}, card2={stored_id_2[:8]}, type={relation_type}"
            )
            return {"success": True, "relation_id": existing.id}
        return {"success": False, "error": "这两张卡片之间已存在关系"}

    # 创建关系
    relation = CardRelation(
        user_id=user_id,
        card_id_1=stored_id_1,
        card_id_2=stored_id_2,
        relation_type=rel_type,
        status=RelationStatus.confirmed,
        similarity_score=None,
    )
    db.add(relation)
    await db.commit()
    await db.refresh(relation)

    logger.info(
        f"手动创建关系: user={user_id[:8]}, "
        f"card1={stored_id_1[:8]}, card2={stored_id_2[:8]}, type={relation_type}"
    )
    return {"success": True, "relation_id": relation.id}


async def delete_relation(
    relation_id: str,
    user_id: str,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    删除一条卡片关系

    验证关系属于该用户后删除。

    Args:
        relation_id: 关系 ID
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        Dict: 操作结果
    """
    result = await db.execute(
        select(CardRelation).where(
            CardRelation.id == relation_id,
            CardRelation.user_id == user_id,
        )
    )
    relation = result.scalars().first()

    if not relation:
        return {"success": False, "error": "关系不存在"}

    await db.delete(relation)
    await db.commit()

    logger.info(f"删除关系: user={user_id[:8]}, relation={relation_id[:8]}")
    return {"success": True, "relation_id": relation_id}


async def suggest_semantic_relations(user_id: str, db: AsyncSession) -> Dict[str, Any]:
    """
    基于 LLM 批量推断知识卡片间的语义关系（prerequisite/subsequent/contrast）

    流程：
    1. 获取用户最多 100 张知识卡片
    2. 查询用户已有的所有 CardRelation（任意状态），构建已存在卡片对集合
    3. 分批（每批 20 张）调用 LLM 推断关系，整批卡片对都已有关系则跳过
    4. 校验 LLM 返回的每条关系（ID 在批次内、relation_type 合法、卡片对未存在），
       创建 suggested 状态的 CardRelation

    Args:
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        Dict: 操作结果，包含 success/new_count/skipped_count/message
    """
    # 局部导入避免影响模块加载时的依赖图
    from ..services.llm_service import LLMService

    # 1. 查询用户的知识卡片，限制 100 张
    cards_result = await db.execute(
        select(KnowledgeCard).where(KnowledgeCard.user_id == user_id).limit(100)
    )
    cards = list(cards_result.scalars().all())

    if len(cards) < 2:
        return {"success": True, "new_count": 0, "skipped_count": 0, "message": "卡片数量不足"}

    # 2. 查询用户已有的所有关系（含 confirmed/suggested/rejected），构建已存在卡片对集合
    existing_result = await db.execute(
        select(CardRelation).where(CardRelation.user_id == user_id)
    )
    existing_relations = list(existing_result.scalars().all())
    existing_pairs: set = set()
    for rel in existing_relations:
        pair_key = tuple(sorted([rel.card_id_1, rel.card_id_2]))
        existing_pairs.add(pair_key)

    # 3. 分批调用 LLM 推断关系
    llm_service = LLMService()
    BATCH_SIZE = 20
    valid_relation_types = {"prerequisite", "subsequent", "contrast"}
    new_count = 0
    skipped_count = 0
    new_relations: List[CardRelation] = []

    for start in range(0, len(cards), BATCH_SIZE):
        batch_cards = cards[start:start + BATCH_SIZE]
        batch_ids = {c.id for c in batch_cards}

        # 先过滤候选对：若该批内所有卡片对都已有关系，则跳过该批
        all_pairs_count = len(batch_cards) * (len(batch_cards) - 1) // 2
        existing_in_batch = 0
        for i in range(len(batch_cards)):
            for j in range(i + 1, len(batch_cards)):
                pair_key = tuple(sorted([batch_cards[i].id, batch_cards[j].id]))
                if pair_key in existing_pairs:
                    existing_in_batch += 1
        if all_pairs_count > 0 and existing_in_batch == all_pairs_count:
            continue

        # 构建卡片摘要
        cards_summary = [
            {
                "id": c.id,
                "title": c.title,
                "card_type": c.card_type.value,
                "content": c.content,
            }
            for c in batch_cards
        ]

        # 调用 LLM 推断关系（单批异常不中断整体流程）
        try:
            relations = await llm_service.infer_card_relations(cards_summary)
        except Exception as e:
            logger.warning(
                f"LLM 推断关系异常(批次 {start // BATCH_SIZE + 1}): "
                f"user={user_id[:8]}, error={e}"
            )
            continue

        # 校验并创建关系
        for rel in relations:
            card_id_a = rel.get("card_id_a")
            card_id_b = rel.get("card_id_b")
            relation_type = rel.get("relation_type")

            # 校验 ID 都在本批卡片集合中
            if card_id_a not in batch_ids or card_id_b not in batch_ids:
                continue

            # 校验关系类型合法
            if relation_type not in valid_relation_types:
                continue

            # 再次校验该卡片对不在已有关系集合中（防 LLM 幻觉）
            pair_key = tuple(sorted([card_id_a, card_id_b]))
            if pair_key in existing_pairs:
                skipped_count += 1
                continue

            # 转换关系类型枚举
            try:
                rel_type = RelationType(relation_type)
            except ValueError:
                continue

            # 有向关系（prerequisite/subsequent）保持 LLM 给的顺序；
            # 无向关系（contrast）按 ID 字典序排序存储
            if rel_type == RelationType.contrast:
                stored_id_1, stored_id_2 = sorted([card_id_a, card_id_b])
            else:
                stored_id_1, stored_id_2 = card_id_a, card_id_b

            new_relation = CardRelation(
                user_id=user_id,
                card_id_1=stored_id_1,
                card_id_2=stored_id_2,
                relation_type=rel_type,
                status=RelationStatus.suggested,
                similarity_score=None,
            )
            new_relations.append(new_relation)
            existing_pairs.add(pair_key)  # 防止同批内重复
            new_count += 1

    # 4. 批量添加并提交
    for rel in new_relations:
        db.add(rel)
    if new_count > 0:
        await db.commit()

    logger.info(
        f"语义关系推断: user={user_id[:8]}, 新增 {new_count} 条建议, 跳过 {skipped_count} 条已有关系"
    )
    return {
        "success": True,
        "new_count": new_count,
        "skipped_count": skipped_count,
        "message": f"新增 {new_count} 条语义关系建议，跳过 {skipped_count} 条已有关系",
    }


# =============================================================================
# 新增：图谱统计、搜索、子图、批量操作、布局保存
# =============================================================================


async def get_graph_stats(user_id: str, db: AsyncSession) -> Dict[str, Any]:
    """
    获取用户知识图谱的统计数据

    返回节点数、边数、按关系类型的分布、孤立节点数等。

    Args:
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        Dict: 包含 total_nodes, total_edges, confirmed_edges, suggested_edges,
              relation_type_distribution, isolated_nodes
    """
    # 卡片总数（聚合查询，避免加载全部卡片行）
    total_nodes = (
        await db.execute(
            select(func.count(KnowledgeCard.id)).where(KnowledgeCard.user_id == user_id)
        )
    ).scalar_one()

    # 各状态关系数（一次聚合按状态分组）
    status_counts: Dict[str, int] = {"confirmed": 0, "suggested": 0, "rejected": 0}
    status_rows = (
        await db.execute(
            select(CardRelation.status, func.count(CardRelation.id))
            .where(CardRelation.user_id == user_id)
            .group_by(CardRelation.status)
        )
    ).all()
    for status, count in status_rows:
        status_counts[status.value] = count

    confirmed = status_counts["confirmed"]
    suggested = status_counts["suggested"]

    # 已确认关系按类型分布（一次聚合按类型分组）
    type_rows = (
        await db.execute(
            select(CardRelation.relation_type, func.count(CardRelation.id))
            .where(
                CardRelation.user_id == user_id,
                CardRelation.status == RelationStatus.confirmed,
            )
            .group_by(CardRelation.relation_type)
        )
    ).all()
    type_distribution = [
        {"relation_type": rel_type.value, "count": count}
        for rel_type, count in sorted(type_rows, key=lambda x: -x[1])
    ]

    # 孤立节点：没有任何关系（confirmed 或 suggested）的卡片，用 NOT EXISTS 精确计数
    rel_exists = exists().where(
        and_(
            CardRelation.user_id == user_id,
            CardRelation.status.in_([RelationStatus.confirmed, RelationStatus.suggested]),
            or_(CardRelation.card_id_1 == KnowledgeCard.id, CardRelation.card_id_2 == KnowledgeCard.id),
        )
    )
    isolated = (
        await db.execute(
            select(func.count(KnowledgeCard.id)).where(
                KnowledgeCard.user_id == user_id,
                ~rel_exists,
            )
        )
    ).scalar_one()

    return {
        "total_nodes": total_nodes,
        "total_edges": confirmed + suggested + status_counts["rejected"],
        "confirmed_edges": confirmed,
        "suggested_edges": suggested,
        "relation_type_distribution": type_distribution,
        "isolated_nodes": isolated,
    }


async def search_nodes(
    user_id: str,
    keyword: str,
    db: AsyncSession,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """
    搜索用户的知识卡片（按标题模糊匹配）

    Args:
        user_id: 用户 ID
        keyword: 搜索关键词
        db: 数据库会话
        limit: 最大返回数

    Returns:
        List[Dict]: 匹配的卡片列表，含 id/title/card_type/note_id/relation_count
    """
    # 单条 SQL：LEFT JOIN 统计每张匹配卡片关联的「不同卡片数」（按卡片对去重），避免逐卡 N+1 查询
    count_cond = and_(
        CardRelation.user_id == user_id,
        CardRelation.status == RelationStatus.confirmed,
        or_(CardRelation.card_id_1 == KnowledgeCard.id, CardRelation.card_id_2 == KnowledgeCard.id),
    )
    # 与 KnowledgeCard.id 相连时取另一端的卡片 id，用于去重计数
    neighbor_expr = case(
        (CardRelation.card_id_1 == KnowledgeCard.id, CardRelation.card_id_2),
        else_=CardRelation.card_id_1,
    )
    rows = (
        await db.execute(
            select(
                KnowledgeCard.id,
                KnowledgeCard.title,
                KnowledgeCard.card_type,
                KnowledgeCard.note_id,
                func.count(distinct(neighbor_expr)).label("relation_count"),
            )
            .outerjoin(CardRelation, count_cond)
            .where(
                KnowledgeCard.user_id == user_id,
                or_(
                    KnowledgeCard.title.ilike(f"%{keyword}%"),
                    KnowledgeCard.content.ilike(f"%{keyword}%"),
                ),
            )
            .group_by(KnowledgeCard.id, KnowledgeCard.title, KnowledgeCard.card_type, KnowledgeCard.note_id)
            .order_by(func.count(distinct(neighbor_expr)).desc())
            .limit(limit)
        )
    ).all()

    return [
        {
            "id": row.id,
            "title": row.title,
            "card_type": row.card_type.value,
            "note_id": row.note_id,
            "relation_count": row.relation_count,
        }
        for row in rows
    ]


async def get_node_subgraph(
    node_id: str,
    user_id: str,
    db: AsyncSession,
) -> Optional[Dict[str, Any]]:
    """
    获取某个节点及其直接邻居的子图

    返回中心节点 + 邻居节点 + 之间的边。

    Args:
        node_id: 中心节点 ID
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        Optional[Dict]: 包含 center_node, neighbor_nodes, edges；节点不存在返回 None
    """
    # 查询中心节点
    card_result = await db.execute(
        select(KnowledgeCard).where(
            KnowledgeCard.id == node_id,
            KnowledgeCard.user_id == user_id,
        )
    )
    center = card_result.scalars().first()
    if not center:
        return None

    # 查询涉及该节点的所有关系
    relations_result = await db.execute(
        select(CardRelation).where(
            CardRelation.user_id == user_id,
            CardRelation.status.in_([RelationStatus.confirmed, RelationStatus.suggested]),
            (CardRelation.card_id_1 == node_id) | (CardRelation.card_id_2 == node_id),
        )
    )
    relations = list(relations_result.scalars().all())

    # 收集邻居节点 ID
    neighbor_ids: set = set()
    edges_data = []
    for rel in relations:
        other_id = rel.card_id_2 if rel.card_id_1 == node_id else rel.card_id_1
        neighbor_ids.add(other_id)
        edges_data.append({
            "id": rel.id,
            "source": rel.card_id_1,
            "target": rel.card_id_2,
            "relation_type": rel.relation_type.value,
            "status": rel.status.value,
            "similarity_score": rel.similarity_score,
        })

    # 查询邻居节点详情
    neighbors = []
    if neighbor_ids:
        neighbors_result = await db.execute(
            select(
                KnowledgeCard.id,
                KnowledgeCard.title,
                KnowledgeCard.card_type,
                KnowledgeCard.note_id,
            ).where(
                KnowledgeCard.id.in_(neighbor_ids),
                KnowledgeCard.user_id == user_id,
            )
        )
        neighbor_cards = list(neighbors_result.all())

        # 批量统计邻居的关联数（按无向卡片对去重，单条查询避免逐邻居 N+1）
        neighbor_rel_rows = (
            await db.execute(
                select(CardRelation.card_id_1, CardRelation.card_id_2)
                .where(
                    CardRelation.user_id == user_id,
                    CardRelation.status == RelationStatus.confirmed,
                    or_(
                        CardRelation.card_id_1.in_(neighbor_ids),
                        CardRelation.card_id_2.in_(neighbor_ids),
                    ),
                )
            )
        ).all()
        neighbor_sets: Dict[str, set] = {nid: set() for nid in neighbor_ids}
        for cid_1, cid_2 in neighbor_rel_rows:
            if cid_1 in neighbor_sets:
                neighbor_sets[cid_1].add(cid_2)
            if cid_2 in neighbor_sets:
                neighbor_sets[cid_2].add(cid_1)

        for n in neighbor_cards:
            neighbors.append({
                "id": n.id,
                "title": n.title,
                "card_type": n.card_type.value,
                "note_id": n.note_id,
                "relation_count": len(neighbor_sets.get(n.id, set())),
            })

    # 统计中心节点关联数：按无向卡片对去重（仅 confirmed）
    center_neighbors: set = set()
    for r in relations:
        if r.status == RelationStatus.confirmed:
            center_neighbors.add(r.card_id_2 if r.card_id_1 == node_id else r.card_id_1)
    center_count = len(center_neighbors)

    return {
        "center_node": {
            "id": center.id,
            "title": center.title,
            "card_type": center.card_type.value,
            "note_id": center.note_id,
            "relation_count": center_count,
        },
        "neighbor_nodes": neighbors,
        "edges": edges_data,
    }


async def batch_confirm_suggestions(
    relation_ids: List[str],
    user_id: str,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    批量确认建议关系

    将所有指定的 suggested 状态关系更新为 confirmed。

    Args:
        relation_ids: 关系 ID 列表
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        Dict: 包含 success、confirmed_count 和 failed_count
    """
    confirmed_count = 0
    failed_count = 0

    # 单条 UPDATE 批量确认（仅更新属于该用户且仍为 suggested 的关系）
    result = await db.execute(
        update(CardRelation)
        .where(
            CardRelation.id.in_(relation_ids),
            CardRelation.user_id == user_id,
            CardRelation.status == RelationStatus.suggested,
        )
        .values(status=RelationStatus.confirmed)
    )
    confirmed_count = result.rowcount
    failed_count = len(relation_ids) - confirmed_count

    if confirmed_count > 0:
        await db.commit()
        logger.info(
            f"批量确认: user={user_id[:8]}, 确认 {confirmed_count} 条, 失败 {failed_count} 条"
        )

    return {
        "success": True,
        "confirmed_count": confirmed_count,
        "failed_count": failed_count,
    }


async def batch_reject_suggestions(
    relation_ids: List[str],
    user_id: str,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    批量拒绝建议关系

    将所有指定的 suggested 状态关系更新为 rejected。

    Args:
        relation_ids: 关系 ID 列表
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        Dict: 包含 success、rejected_count 和 failed_count
    """
    rejected_count = 0
    failed_count = 0

    # 单条 UPDATE 批量拒绝（仅更新属于该用户且仍为 suggested 的关系）
    result = await db.execute(
        update(CardRelation)
        .where(
            CardRelation.id.in_(relation_ids),
            CardRelation.user_id == user_id,
            CardRelation.status == RelationStatus.suggested,
        )
        .values(status=RelationStatus.rejected)
    )
    rejected_count = result.rowcount
    failed_count = len(relation_ids) - rejected_count

    if rejected_count > 0:
        await db.commit()
        logger.info(
            f"批量拒绝: user={user_id[:8]}, 拒绝 {rejected_count} 条, 失败 {failed_count} 条"
        )

    return {
        "success": True,
        "rejected_count": rejected_count,
        "failed_count": failed_count,
    }
