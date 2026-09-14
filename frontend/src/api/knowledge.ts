/**
 * @file 知识点管理 API
 * @description 联合分析、拓展知识点、卡片标记、盲点与掌握度查询
 */
import { request, type KnowledgeCard } from './client';
import { buildQuery } from './query';
import type { BodyOf, QueryOf, Schema } from './generated/types';

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

/**
 * 标记/取消标记重点、难点的请求体（阶段 5.1 / S3：`PATCH /knowledge/cards/{card_id}/mark`）
 *
 * 契约 `CardMarkRequest` 的两个字段都可选且**没有 `default`**（`Optional[bool] = None`），
 * 所以不需要 `BodyWithDefaults`：省略字段 = 后端不改这个字段
 * （后端 `mark_card`：`if req.is_key_point is not None: …`）。
 * 传 `null` 与不传在运行时同样等价。
 */
export type MarkCardPayload = BodyOf<'/knowledge/cards/{card_id}/mark', 'patch'>;

/** 触发联合分析 */
export async function extractCombined(linkId: string): Promise<CombinedExtractResponse> {
  return request<CombinedExtractResponse>(`/knowledge/links/${linkId}/extract-combined`, {
    method: 'POST',
  });
}

/** 生成拓展知识点 */
export async function generateExtension(
  cardId: string,
  materialNoteId?: string,
): Promise<ExtensionGenerateResponse> {
  // `POST /knowledge/cards/{card_id}/generate-extension` 的请求体：契约只有
  // `material_note_id?: string | null`；`materialNoteId` 为 undefined 时
  // JSON.stringify 会省略该字段（保持原有行为）
  const body: BodyOf<'/knowledge/cards/{card_id}/generate-extension', 'post'> = {
    material_note_id: materialNoteId,
  };
  return request<ExtensionGenerateResponse>(`/knowledge/cards/${cardId}/generate-extension`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/** 为拓展卡片立即出题（生成自 `ExtensionQuestionsTriggeredResponse`，含 `target_categories`） */
export async function generateExtensionQuestions(
  cardId: string,
): Promise<ExtensionQuestionsTriggeredResponse> {
  return request(`/knowledge/cards/${cardId}/generate-questions`, { method: 'POST' });
}

/** 标记/取消标记重点、难点（`data` 的类型由契约派生，见 `MarkCardPayload`） */
export async function markCard(cardId: string, data: MarkCardPayload): Promise<KnowledgeCard> {
  return request<KnowledgeCard>(`/knowledge/cards/${cardId}/mark`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

/** 获取盲点列表（参数对象由契约派生：键名/取值域以 `GET /api/knowledge/blind-spots` 为准） */
export async function getBlindSpots(
  params: QueryOf<'/knowledge/blind-spots', 'get'> = {},
): Promise<BlindSpotListResponse> {
  // ⚠️ 键名**逐个写出来**，而不是 `buildQuery(params)` 透传：
  // 漂移脚本靠这些字面量核对"前端传的参数名在契约里存在"，
  // 透传一个变量会让它一个名字都扫不到 —— 实测透传时
  // `queryNameDetections` 54 → 47、`unusedSchemaQuery` 3 → 10（看着像变坏，其实是没比）。
  const query: QueryOf<'/knowledge/blind-spots', 'get'> = {
    link_id: params.link_id,
    material_id: params.material_id,
    page: params.page,
    page_size: params.page_size,
  };
  return request<BlindSpotListResponse>(`/knowledge/blind-spots${buildQuery(query)}`);
}

/** 获取掌握度概览（参数对象由契约派生：`GET /api/knowledge/mastery`） */
export async function getMasteryOverview(
  params: QueryOf<'/knowledge/mastery', 'get'> = {},
): Promise<MasteryOverviewResponse> {
  // 同 getBlindSpots：键名逐个写出来，别透传（理由见上）
  const query: QueryOf<'/knowledge/mastery', 'get'> = {
    page: params.page,
    page_size: params.page_size,
    card_category: params.card_category,
  };
  return request<MasteryOverviewResponse>(`/knowledge/mastery${buildQuery(query)}`);
}

/**
 * 触发语义关系推断（在 graph 路由下；生成自 `SemanticRelationsResponse`）
 *
 * 这是 §7.1 里唯一一个 `additionalProperties` 空壳（`SCHEMA_LOOSE`）端点：
 * P1 把后端的 `Dict[str, Any]` 换成了真实模型，切换后它不再靠手写维护。
 */
export async function suggestSemanticRelations(): Promise<SemanticRelationsResponse> {
  return request('/graph/suggest-semantic', { method: 'POST' });
}
