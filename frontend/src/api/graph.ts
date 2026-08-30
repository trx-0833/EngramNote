/**
 * @file 知识图谱 API
 * @description 图谱节点/边、建议关系、关系确认/拒绝与子图查询。
 */
import { request } from './client'

/** 图谱节点，对应一张知识卡片 */
export interface GraphNode {
  /** 节点唯一标识（卡片 ID） */
  id: string;
  /** 卡片标题 */
  title: string;
  /** 卡片类型：concept / formula / qa / definition */
  card_type: string;
  /** 所属笔记 ID */
  note_id: string;
  /** 关联边数量 */
  relation_count: number;
  /** 所属笔记是否在回收站中，前端渲染时过滤回收站节点 */
  note_trashed?: boolean;
}

/** 图谱边，对应卡片间的关系 */
export interface GraphEdge {
  /** 边唯一标识 */
  id: string;
  /** 起点节点 ID */
  source: string;
  /** 终点节点 ID */
  target: string;
  /** 关系类型：related / prerequisite / subsequent / contrast */
  relation_type: string;
  /** 边状态：suggested / confirmed */
  status: string;
  /** 相似度分数 */
  similarity_score: number | null;
}

/** 图谱数据，包含节点和边 */
export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

/** 建议关系 */
export interface SuggestedRelation {
  /** 建议关系 ID */
  id: string;
  /** 卡片 1 ID */
  card_id_1: string;
  /** 卡片 2 ID */
  card_id_2: string;
  /** 卡片 1 标题 */
  card_1_title: string;
  /** 卡片 2 标题 */
  card_2_title: string;
  /** 相似度分数 */
  similarity_score: number;
}

/**
 * 获取知识图谱数据
 * 返回所有节点和边，用于力导向图可视化。
 *
 * @returns 图谱数据
 */
export async function getGraphData(): Promise<GraphData> {
  return request<GraphData>('/graph');
}

/**
 * 获取建议关系列表
 * 返回系统自动检测到的潜在关联，供用户确认或拒绝。
 *
 * @returns 建议关系列表
 */
export async function getSuggestions(): Promise<SuggestedRelation[]> {
  // 后端返回的是纯数组（response_model=list[SuggestedRelation]），不是 { items } 包装
  return request<SuggestedRelation[]>('/graph/suggestions');
}

/**
 * 手动触发相关关系建议生成
 * 基于嵌入向量相似度挖掘新的潜在关联（卡片较多时可能耗时数十秒）。
 *
 * @returns 新增建议数量
 */
export async function suggestRelations(): Promise<{ success: boolean; new_count: number }> {
  return request<{ success: boolean; new_count: number }>('/graph/suggest', {
    method: 'POST',
  });
}

/** 图谱操作统一返回结构（success + 可选附加字段） */
export interface GraphOperationResult {
  success: boolean;
  error?: string;
  new_count?: number;
  message?: string;
}

/**
 * 确认建议关系
 * 将 suggested 状态的边转为 confirmed。
 *
 * @param relationId - 建议关系 ID
 */
export async function confirmRelation(relationId: string): Promise<GraphOperationResult> {
  return request<GraphOperationResult>('/graph/confirm', {
    method: 'POST',
    body: JSON.stringify({ relation_id: relationId }),
  });
}

/**
 * 拒绝建议关系
 * 删除 suggested 状态的边。
 *
 * @param relationId - 建议关系 ID
 */
export async function rejectRelation(relationId: string): Promise<GraphOperationResult> {
  return request<GraphOperationResult>('/graph/reject', {
    method: 'POST',
    body: JSON.stringify({ relation_id: relationId }),
  });
}

/**
 * 创建卡片间关系
 * 手动在两张卡片之间建立指定类型的关联。
 *
 * @param cardId1 - 卡片 1 ID
 * @param cardId2 - 卡片 2 ID
 * @param relationType - 关系类型
 */
export async function createRelation(cardId1: string, cardId2: string, relationType: string): Promise<GraphOperationResult> {
  return request<GraphOperationResult>('/graph/relation', {
    method: 'POST',
    body: JSON.stringify({ card_id_1: cardId1, card_id_2: cardId2, relation_type: relationType }),
  });
}

/**
 * 删除关系
 * 删除已确认的关系边。
 *
 * @param relationId - 关系 ID
 */
export async function deleteRelation(relationId: string): Promise<GraphOperationResult> {
  return request<GraphOperationResult>(`/graph/relation/${relationId}`, {
    method: 'DELETE',
  });
}

/** 图谱统计数据 */
export interface GraphStats {
  total_nodes: number;
  total_edges: number;
  confirmed_edges: number;
  suggested_edges: number;
  relation_type_distribution: Array<{ relation_type: string; count: number }>;
  isolated_nodes: number;
}

/**
 * 获取知识图谱统计数据
 */
export async function getGraphStats(): Promise<GraphStats> {
  return request<GraphStats>('/graph/stats');
}

/** 图搜索节点 */
export interface GraphSearchNode {
  id: string;
  title: string;
  card_type: string;
  note_id: string;
  relation_count: number;
}

/** 图搜索结果 */
export interface GraphSearchResult {
  items: GraphSearchNode[];
  total: number;
}

/**
 * 搜索图谱中的节点
 *
 * @param keyword - 搜索关键词
 * @param limit - 最大返回数量
 */
export async function searchGraphNodes(keyword: string, limit = 20): Promise<GraphSearchResult> {
  const params = new URLSearchParams({ q: keyword, limit: String(limit) });
  return request<GraphSearchResult>(`/graph/search?${params}`);
}

/** 节点子图响应 */
export interface NodeSubgraph {
  center_node: GraphNode;
  neighbor_nodes: GraphNode[];
  edges: GraphEdge[];
}

/**
 * 获取某个节点及其直接邻居的子图
 *
 * @param nodeId - 节点 ID
 */
export async function getNodeSubgraph(nodeId: string): Promise<NodeSubgraph> {
  return request<NodeSubgraph>(`/graph/node/${nodeId}/subgraph`);
}

/**
 * 批量确认建议关系
 *
 * @param relationIds - 建议关系 ID 列表
 */
export async function batchConfirmRelations(relationIds: string[]): Promise<GraphOperationResult> {
  return request<GraphOperationResult>('/graph/batch-confirm', {
    method: 'POST',
    body: JSON.stringify({ relation_ids: relationIds }),
  });
}

/**
 * 批量拒绝建议关系
 *
 * @param relationIds - 建议关系 ID 列表
 */
export async function batchRejectRelations(relationIds: string[]): Promise<GraphOperationResult> {
  return request<GraphOperationResult>('/graph/batch-reject', {
    method: 'POST',
    body: JSON.stringify({ relation_ids: relationIds }),
  });
}