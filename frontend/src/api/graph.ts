/**
 * @file 知识图谱 API
 * @description 图谱节点/边、建议关系、关系确认/拒绝与子图查询。
 */
import { request } from './client';
import { buildQuery } from './query';
import type { BodyOf, QueryOf, Schema } from './generated/types';

// --- 图谱相关类型（阶段 5.1 / S2：来源已改为 OpenAPI 生成类型）---

/**
 * 图谱节点，对应一张知识卡片（生成自 `GraphNode`）
 *
 * ⚠️ `note_id` 现在是 `?: string | null`：库里 `KnowledgeCard.note_id` **就是**
 * nullable（物理删除笔记时"提升核心卡片"会把卡片变成独立节点），
 * 契约只是如实声明。`card_type` 也收窄成 `CardType` 枚举。
 */
export type GraphNode = Schema<'GraphNode'>;

/**
 * 图谱边，对应卡片间的关系（生成自 `GraphEdge`）
 *
 * ⚠️ `relation_type` / `status` 现在是枚举（`RelationType` / `RelationStatus`）；
 * `type` 是后端如实声明的可选字段（两条构建路径都不产出它）。
 */
export type GraphEdge = Schema<'GraphEdge'>;

/** 图谱数据，包含节点和边（生成自 `GraphData`） */
export type GraphData = Schema<'GraphData'>;

/** 建议关系（生成自 `SuggestedRelation`） */
export type SuggestedRelation = Schema<'SuggestedRelation'>;

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

// ⚠️ 这里**没有**"图谱操作统一返回结构"了（阶段 5.1 / S2 删掉了它）。
//
// 手写的 `GraphOperationResult`（`success` + 4 个全可选字段）用一个"万能可选"
// 形状盖住了后端**四种互不相同的**返回：`{success, relation_id}` /
// `{success, new_count}` / `{success, confirmed_count, failed_count}` /
// `{success, rejected_count, failed_count}`。代价是双向的：已知字段被写成可选，
// 未知字段被静默丢掉（`.relation_id` 连类型提示都没有）。
// 现在每个函数各自指向它那个端点的生成类型，**契约说什么就是什么**。
//
// 这两个类型按"关系操作"与"批量操作"分开，与后端模型一一对应：
// `GraphRelationOperationResponse` / `GraphBatchOperationResponse`。

/** 单个关系操作（confirm / reject / create / delete）的响应（生成自 `GraphRelationOperationResponse`） */
export type GraphRelationOperationResponse = Schema<'GraphRelationOperationResponse'>;

/** 批量确认/拒绝的响应（生成自 `GraphBatchOperationResponse`；`success` + 计数，无 `relation_id`） */
export type GraphBatchOperationResponse = Schema<'GraphBatchOperationResponse'>;

/**
 * 确认建议关系
 * 将 suggested 状态的边转为 confirmed。
 *
 * @param relationId - 建议关系 ID
 */
export async function confirmRelation(relationId: string): Promise<GraphRelationOperationResponse> {
  // `POST /graph/confirm` 的请求体：契约 `{ relation_id: string }`
  const body: BodyOf<'/graph/confirm', 'post'> = { relation_id: relationId };
  return request<GraphRelationOperationResponse>('/graph/confirm', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/**
 * 拒绝建议关系
 * 删除 suggested 状态的边。
 *
 * @param relationId - 建议关系 ID
 */
export async function rejectRelation(relationId: string): Promise<GraphRelationOperationResponse> {
  // `POST /graph/reject` 的请求体：契约 `{ relation_id: string }`
  const body: BodyOf<'/graph/reject', 'post'> = { relation_id: relationId };
  return request<GraphRelationOperationResponse>('/graph/reject', {
    method: 'POST',
    body: JSON.stringify(body),
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
export async function createRelation(
  cardId1: string,
  cardId2: string,
  relationType: string,
): Promise<GraphRelationOperationResponse> {
  // `POST /graph/relation` 的请求体：契约三个字段全必填，且 `relation_type` 在契约里
  // 就是 `string`（后端模型没写成枚举），这里不额外收窄
  const body: BodyOf<'/graph/relation', 'post'> = {
    card_id_1: cardId1,
    card_id_2: cardId2,
    relation_type: relationType,
  };
  return request<GraphRelationOperationResponse>('/graph/relation', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/**
 * 删除关系
 * 删除已确认的关系边。
 *
 * @param relationId - 关系 ID
 */
export async function deleteRelation(relationId: string): Promise<GraphRelationOperationResponse> {
  return request<GraphRelationOperationResponse>(`/graph/relation/${relationId}`, {
    method: 'DELETE',
  });
}

/** 图谱统计数据（生成自 `GraphStats`） */
export type GraphStats = Schema<'GraphStats'>;

/**
 * 获取知识图谱统计数据
 */
export async function getGraphStats(): Promise<GraphStats> {
  return request<GraphStats>('/graph/stats');
}

/**
 * 图搜索节点（生成自 `GraphSearchResult`）
 *
 * ⚠️ 名字与后端模型**不是**同名关系：后端的 `GraphSearchResult` 是"单条结果"，
 * 外层信封才叫 `GraphSearchResponse`。前端保留 `GraphSearchNode` 这个名字，
 * 因为 4 个调用方（drawNode / GraphToolbar / useCanvasObjects / useGraphSearch）
 * 都按它引入 —— 改名会让 S2 从"零调用方改动"变成一次全量重命名。
 */
export type GraphSearchNode = Schema<'GraphSearchResult'>;

/** 图搜索结果信封（生成自 `GraphSearchResponse`） */
export type GraphSearchResult = Schema<'GraphSearchResponse'>;

/**
 * 搜索图谱中的节点
 *
 * @param keyword - 搜索关键词
 * @param limit - 最大返回数量
 */
export async function searchGraphNodes(keyword: string, limit = 20): Promise<GraphSearchResult> {
  // 查询参数由契约派生（阶段 5.1 / S3b）：`GET /api/graph/search` 是 `q` + `limit`
  const query: QueryOf<'/graph/search', 'get'> = { q: keyword, limit };
  return request<GraphSearchResult>(`/graph/search${buildQuery(query)}`);
}

/** 节点子图响应（生成自 `NodeSubgraph`） */
export type NodeSubgraph = Schema<'NodeSubgraph'>;

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
export async function batchConfirmRelations(
  relationIds: string[],
): Promise<GraphBatchOperationResponse> {
  // `POST /graph/batch-confirm` 的请求体：契约 `{ relation_ids: string[] }`
  const body: BodyOf<'/graph/batch-confirm', 'post'> = { relation_ids: relationIds };
  return request<GraphBatchOperationResponse>('/graph/batch-confirm', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/**
 * 批量拒绝建议关系
 *
 * @param relationIds - 建议关系 ID 列表
 */
export async function batchRejectRelations(
  relationIds: string[],
): Promise<GraphBatchOperationResponse> {
  // `POST /graph/batch-reject` 的请求体：契约 `{ relation_ids: string[] }`
  const body: BodyOf<'/graph/batch-reject', 'post'> = { relation_ids: relationIds };
  return request<GraphBatchOperationResponse>('/graph/batch-reject', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}
