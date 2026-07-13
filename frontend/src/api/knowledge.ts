/**
 * @file 知识点管理 API
 * @description 联合分析、拓展知识点、卡片标记、盲点与掌握度查询
 */
import { request, type KnowledgeCard } from './client'

/** 联合分析响应 */
export interface CombinedExtractResponse {
  link_id: string
  material_note_id: string
  personal_note_id: string
  regular_count: number
  blind_spot_count: number
  total_cards: number
  message: string
}

/** 拓展生成响应 */
export interface ExtensionGenerateResponse {
  parent_card_id: string
  extension_cards: KnowledgeCard[]
  message: string
}

/** 盲点列表响应 */
export interface BlindSpotListResponse {
  items: KnowledgeCard[]
  total: number
  page: number
  page_size: number
}

/** 掌握度概览条目 */
export interface MasteryOverviewItem {
  card_id: string
  title: string
  card_category: 'regular' | 'blind_spot' | 'extension'
  mastery_level: number
  is_key_point: boolean
  is_difficulty: boolean
  review_count: number
}

/** 掌握度概览响应 */
export interface MasteryOverviewResponse {
  items: MasteryOverviewItem[]
  total: number
  average_mastery: number
}

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

/** 为拓展卡片立即出题 */
export async function generateExtensionQuestions(cardId: string): Promise<{ card_id: string; note_id: string; message: string }> {
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

/** 触发语义关系推断（在 graph 路由下） */
export async function suggestSemanticRelations(): Promise<{ success: boolean; new_count: number; skipped_count: number; message: string }> {
  return request('/graph/suggest-semantic', { method: 'POST' })
}
