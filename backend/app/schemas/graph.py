"""知识图谱模块 Pydantic Schema

定义知识图谱相关的请求/响应模型，包括图节点、边、
建议关系及关系的确认/拒绝/创建请求，以及增强的统计、搜索和子图功能。
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
    # 所属笔记是否在回收站中（悬挂引用/独立卡片为 False），前端据此过滤回收站节点
    note_trashed: bool = False


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


class BatchConfirmRequest(BaseModel):
    """批量确认建议关系请求"""
    relation_ids: List[str]


class BatchRejectRequest(BaseModel):
    """批量拒绝建议关系请求"""
    relation_ids: List[str]


# --- 图谱统计 ---

class RelationTypeCount(BaseModel):
    """按关系类型的统计"""
    relation_type: str
    count: int


class GraphStats(BaseModel):
    """知识图谱统计数据"""
    total_nodes: int
    total_edges: int
    confirmed_edges: int
    suggested_edges: int
    relation_type_distribution: List[RelationTypeCount]
    isolated_nodes: int  # 孤立节点（无任何关系）


# --- 节点子图 ---

class NodeSubgraph(BaseModel):
    """某个节点及其直接邻居的子图"""
    center_node: GraphNode
    neighbor_nodes: List[GraphNode]
    edges: List[GraphEdge]


# --- 图搜索结果 ---

class GraphSearchResult(BaseModel):
    """图搜索单条结果"""
    id: str
    title: str
    card_type: CardType
    note_id: str
    relation_count: int


class GraphSearchResponse(BaseModel):
    """图搜索结果"""
    items: List[GraphSearchResult]
    total: int


# --- 节点位置持久化 ---

class NodePosition(BaseModel):
    """节点位置信息"""
    node_id: str
    x: float
    y: float


class SaveLayoutRequest(BaseModel):
    """保存图谱布局请求"""
    positions: List[NodePosition]
