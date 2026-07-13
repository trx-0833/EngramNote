"""
知识图谱模块 Pydantic Schema

定义知识图谱相关的请求/响应模型，包括图节点、边、
建议关系及关系的确认/拒绝/创建请求。
"""

from typing import List, Optional

from pydantic import BaseModel

from ..models.card_relation import RelationType, RelationStatus
from ..models.knowledge_card import CardType


# --- 图数据响应模型 ---

class GraphNode(BaseModel):
    """图节点，对应一张知识卡片"""
    id: str
    title: str
    card_type: CardType
    note_id: str
    relation_count: int = 0


class GraphEdge(BaseModel):
    """图边，对应一条卡片关系"""
    id: str
    source: str
    target: str
    relation_type: RelationType
    status: RelationStatus
    similarity_score: Optional[float] = None


class GraphData(BaseModel):
    """完整图数据，包含所有节点和边"""
    nodes: List[GraphNode]
    edges: List[GraphEdge]


# --- 建议关系响应模型 ---

class SuggestedRelation(BaseModel):
    """自动建议的关系，附带卡片标题便于展示"""
    id: str
    card_id_1: str
    card_id_2: str
    card_1_title: str
    card_2_title: str
    similarity_score: Optional[float] = None


# --- 关系操作请求模型 ---

class ConfirmRelationRequest(BaseModel):
    """确认建议关系请求"""
    relation_id: str


class RejectRelationRequest(BaseModel):
    """拒绝建议关系请求"""
    relation_id: str


class CreateRelationRequest(BaseModel):
    """手动创建关系请求"""
    card_id_1: str
    card_id_2: str
    relation_type: str
