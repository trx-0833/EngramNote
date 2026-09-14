/**
 * @file 知识点管理 API
 * @description 联合分析、拓展知识点、卡片标记、盲点与掌握度查询
 */
import { request, type KnowledgeCard } from './client'
import type { Schema } from './generated/types'

// --- 知识点相关类型（阶段 5.1 / S2：来源已改为 OpenAPI 生成类型）---

/** 联合分析响应（生成自 `CombinedExtractResponse`） */
export type CombinedExtractResponse = Schema<'CombinedExtractResponse'>;

/** 拓展生成响应（生成自 `ExtensionGenerateResponse`） */
export type ExtensionGenerateResponse = Schema<'ExtensionGenerateResponse'>;

/** 盲点列表响应（生成自 `BlindSpotListResponse`） */
export type BlindSpotListResponse = Schema<'BlindSpotListResponse'>;

/** 掌握度概览条目（生成自 `MasteryOverviewItem`） */
export type MasteryOverviewItem = Schema<'MasteryOverviewItem'>;

/** 掌握度概览响应（生成自 `MasteryOverviewResponse`） */
export type MasteryOverviewResponse = Schema<'MasteryOverviewResponse'>;

/**
 * 为拓展卡片立即出题的回执（生成自 `ExtensionQuestionsTriggeredResponse`）
 *
 * `target_categories` 带默认值 → 生成类型里是必填；目前恒为 `["extension"]`。
 */
export type ExtensionQuestionsTriggeredResponse = Schema<'ExtensionQuestionsTriggeredResponse'>;

/** 语义关系推断结果（生成自 `SemanticRelationsResponse`） */
export type SemanticRelationsResponse = Schema<'SemanticRelationsResponse'>;

/** 触发联合分析 */
export async function extractCombined(linkId: string): Promise<CombinedExtractResponse> {
  return request<CombinedExtractResponse>(`/knowledge/links/${linkId}/extract-combined`, { method: 'POST' })
}

/** 生成拓展知识点 */
export async function generateExtension(cardId: string, materialNoteId?: string): Promise<ExtensionGenerateResponse> {
  return request<ExtensionGenerateResponse>(`/knowledge/cards/${cardId}/generate-extension`, {
    method: 'POST',
    body: JSON.stringify({ material_note_id: materialNoteId }),
  })
}

/** 为拓展卡片立即出题（生成自 `ExtensionQuestionsTriggeredResponse`，含 `target_categories`） */
export async function generateExtensionQuestions(cardId: string): Promise<ExtensionQuestionsTriggeredResponse> {
  return request(`/knowledge/cards/${cardId}/generate-questions`, { method: 'POST' })
}

/** 标记/取消标记重点、难点 */
export async function markCard(cardId: string, data: { is_key_point?: boolean; is_difficulty?: boolean }): Promise<KnowledgeCard> {
  return request<KnowledgeCard>(`/knowledge/cards/${cardId}/mark`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  })
}

/** 获取盲点列表 */
export async function getBlindSpots(params: { link_id?: string; material_id?: string; page?: number; page_size?: number } = {}): Promise<BlindSpotListResponse> {
  const query = new URLSearchParams()
  if (params.link_id) query.set('link_id', params.link_id)
  if (params.material_id) query.set('material_id', params.material_id)
  if (params.page) query.set('page', String(params.page))
  if (params.page_size) query.set('page_size', String(params.page_size))
  return request<BlindSpotListResponse>(`/knowledge/blind-spots?${query}`)
}

/** 获取掌握度概览 */
export async function getMasteryOverview(params: { page?: number; page_size?: number; card_category?: string } = {}): Promise<MasteryOverviewResponse> {
  const query = new URLSearchParams()
  if (params.page) query.set('page', String(params.page))
  if (params.page_size) query.set('page_size', String(params.page_size))
  if (params.card_category) query.set('card_category', params.card_category)
  return request<MasteryOverviewResponse>(`/knowledge/mastery?${query}`)
}

/**
 * 触发语义关系推断（在 graph 路由下；生成自 `SemanticRelationsResponse`）
 *
 * 这是 §7.1 里唯一一个 `additionalProperties` 空壳（`SCHEMA_LOOSE`）端点：
 * P1 把后端的 `Dict[str, Any]` 换成了真实模型，切换后它不再靠手写维护。
 */
export async function suggestSemanticRelations(): Promise<SemanticRelationsResponse> {
  return request('/graph/suggest-semantic', { method: 'POST' })
}
