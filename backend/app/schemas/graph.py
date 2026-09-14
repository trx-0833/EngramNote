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
    #: 所属笔记 ID。
    #:
    #: ⚠️ **可空，而且是真实的可空**（阶段 5.1 修复时发现）：
    #: `KnowledgeCard.note_id` 在库里就是 nullable（物理删除笔记时勾选
    #: "提升核心卡片"会把它置 NULL，卡片成为图谱独立节点），而
    #: `graph_service.get_node_subgraph` 直接把 `center.note_id` 原样放进返回字典。
    #: 声明成 `str` 时 `GET /graph` 之所以没炸，是因为那条路径**构造的是
    #: `GraphNode(...)` 实例**而不是走校验 —— 它在 pydantic v2 里不做类型强制。
    #: 本模型现在会校验，因此必须如实标成可空：否则"独立卡片"一旦出现，
    #: `GET /graph/node/{id}/subgraph` 会 500。**这是修契约，不是改行为**——
    #: 可空字段为 null 时序列化结果与非校验路径完全一致。
    note_id: Optional[str] = None
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
    #: 预留的边类型标注。**当前后端两条构建路径都不产出它**：
    #: - `graph_service.get_graph_data` 构造 `GraphEdge(...)` 时没有传；
    #: - `graph_service.get_node_subgraph` 手工拼 `edges_data` 时也没有。
    #: 因此它在运行时是 `undefined`（`response_model` 会把缺失的可选字段
    #: 序列化成 `null`，见下方说明）。本阶段**不改行为**，如实声明为可选即可。
    #:
    #: ⚠️ 这里同时暴露一处**序列化口径变化**（属"模型本身"而非"响应体形状"）：
    #: `GraphData` 走的 `get_graph_data` 是用 `GraphEdge(...)` 直接构造实例，
    #: 不经校验，缺省字段**不会**被写进 JSON；而 `NodeSubgraph` 是从 dict 校验的，
    #: 缺省字段会被补成 `null`。于是 `GET /graph/node/{id}/subgraph` 的 edges 里
    #: 会多出 `"type": null` / `"similarity_score"` 本来就有的 `null`。
    #: 这是"新增 response_model 后 FastAPI 的正常行为"（本项目其它列表端点也一样），
    #: 对前端是**加法**：`edge.type` 从 `undefined` 变成 `null`，两者都是假值。
    type: Optional[str] = None


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
    # 同 GraphNode.note_id：库里可空，如实声明（见 GraphNode 的说明）
    note_id: Optional[str] = None
    relation_count: int


class GraphSearchResponse(BaseModel):
    """图搜索结果"""
    items: List[GraphSearchResult]
    total: int


# --- 关系操作响应（阶段 5.1：此前这 8 个端点没有 response_model）---
#
# 以下 4 个模型的字段**逐个对着 graph_service 的返回字典抄**（见各模型的
# 出处注释）。刻意不给"可能不存在的字段"留 optional 默认值：
# 契约的价值就在于"声明了的一定有"，把实际恒在的字段写成可选会让前端白写判空。

class GraphRelationOperationResponse(BaseModel):
    """单条关系操作结果（确认 / 拒绝 / 手动创建 / 删除）

    出处（均为 4 处成功分支的 return）：
    - `graph_service.confirm_relation`  → `{"success": True, "relation_id": relation_id}`
    - `graph_service.reject_relation`   → 同上
    - `graph_service.create_relation`   → `{"success": True, "relation_id": relation.id}`
    - `graph_service.delete_relation`   → 同上

    失败分支不在这里：`result["success"]` 为 False 时 API 层已经抛
    HTTPException（400 / 404），响应体走统一错误信封 `{detail, error_code, request_id}`。
    因此 `success` 在**成功响应**里恒为 True —— 保留它是因为客户端已经在读这个字段，
    把它去掉等于单方面改契约。
    """

    success: bool
    relation_id: str


class GraphSuggestResponse(BaseModel):
    """基于嵌入相似度的关系建议结果

    出处：`graph.py:161`（`suggest_relations_api` 的 return），
    服务层 `graph_service.auto_suggest_relations` 只返回一个 int（新增条数）。
    """

    success: bool
    new_count: int


class GraphBatchOperationResponse(BaseModel):
    """批量确认 / 拒绝建议关系的结果

    出处：
    - `graph_service.batch_confirm_suggestions` → `{success, confirmed_count, failed_count}`
    - `graph_service.batch_reject_suggestions`  → `{success, rejected_count, failed_count}`

    两个字段都**有默认值 0**：两个端点各自只填自己那一个计数，
    但返回字典里两个键都恒在（另一个是 0）。声明成两个独立可选字段会让
    "批量拒绝的响应里有没有 confirmed_count" 变成一个需要读实现才能回答的问题。
    """

    success: bool
    confirmed_count: int = 0
    rejected_count: int = 0
    failed_count: int = 0


class SemanticRelationsResponse(BaseModel):
    """LLM 语义关系推断的结果

    出处：`graph_service.suggest_semantic_relations` 的两处 return
    （`graph.py:300` 的端点直接原样转发）：

    - 卡片不足 2 张：`{success: True, new_count: 0, skipped_count: 0, message: "卡片数量不足"}`
    - 正常结束：`{success, new_count, skipped_count, message}`（message 是拼出来的中文摘要）

    ⚠️ 这个端点在漂移报告里被记为 `SCHEMA_LOOSE`（`Dict[str, Any]` →
    `additionalProperties: true`），它是本轮 22 个端点里唯一一个
    "看着有声明、实际等于没声明"的。
    """

    success: bool
    new_count: int
    skipped_count: int
    message: str


# --- 节点位置持久化 ---

class NodePosition(BaseModel):
    """节点位置信息"""
    node_id: str
    x: float
    y: float


class SaveLayoutRequest(BaseModel):
    """保存图谱布局请求"""
    positions: List[NodePosition]
