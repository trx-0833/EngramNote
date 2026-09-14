/**
 * @file 理解管道与问答 API
 * @description 触发理解、状态查询、章节摘要、知识卡片、题目生成与 RAG 问答。
 */
import { request } from './client'
import type { Schema } from './generated/types'

// --- 理解管道与问答相关类型（阶段 5.1 / S2：来源已改为 OpenAPI 生成类型）---

/**
 * 知识卡片（生成自 `KnowledgeCardResponse`）
 *
 * ⚠️ `card_type` 现在是 `CardType` 枚举（concept / formula / qa / definition），
 * 不再是 `string`。
 */
export type KnowledgeCard = Schema<'KnowledgeCardResponse'>;

/** 知识卡片列表响应（生成自 `KnowledgeCardListResponse`） */
export type KnowledgeCardListResponse = Schema<'KnowledgeCardListResponse'>;

/**
 * 题目（生成自 `QuizItemResponse`）
 *
 * ⚠️ `question_type` / `difficulty` 现在是枚举
 * （`QuestionType`：choice / fill_blank / short_answer；`DifficultyLevel`：easy / medium / hard）。
 */
export type QuizItem = Schema<'QuizItemResponse'>;

/** 题目列表响应（生成自 `QuizItemListResponse`） */
export type QuizItemListResponse = Schema<'QuizItemListResponse'>;

/** 理解管道影响面（生成自 `UnderstandingImpact`） */
export type UnderstandingImpact = Schema<'UnderstandingImpact'>;

/**
 * 理解管道触发响应（生成自 `UnderstandingStartResponse`）
 *
 * `requires_confirm` 带默认值 → 生成类型里是**必填**：
 * archived 笔记未确认时后端一定会给出这个字段
 * （确认后带 confirm=true 重调，见 docs/decisions.md#F-02）。
 */
export type UnderstandingStartResponse = Schema<'UnderstandingStartResponse'>;

/** 理解管道状态响应（生成自 `UnderstandingStatusResponse`；`status` 现在是 `NoteStatus`） */
export type UnderstandingStatusResponse = Schema<'UnderstandingStatusResponse'>;

/** 章节摘要（生成自 `ChapterSummary`） */
export type ChapterSummary = Schema<'ChapterSummary'>;

/** 章节摘要列表响应（生成自 `ChapterSummaryListResponse`） */
export type ChapterSummaryListResponse = Schema<'ChapterSummaryListResponse'>;

/**
 * 卡片去重建议列表（生成自 `CardDuplicateListResponse`）
 *
 * ⚠️ 每一条候选的 `score`（原始 n-gram 分，**排序键**）此前不在前端类型里 ——
 * "为什么这条排第一"当时只有后端知道。
 */
export type CardDuplicateListResponse = Schema<'CardDuplicateListResponse'>;

/** 问答请求（生成自 `QuestionRequest`） */
export type QuestionRequest = Schema<'QuestionRequest'>;

/**
 * 问答引用来源（生成自 `AnswerSource`）
 *
 * 定位字段（阶段 2.7：引用可回跳）在契约里全部是 `?: T | null` ——
 * 检索降级或命中历史数据时可能缺失。缺失时应**退化为只显示来源、不提供跳转**，
 * 而不是跳到错误位置。
 *
 * ⚠️ 同一份 `AnswerSource` 也藏在 SSE 的 `sources` 事件里（两个流式端点），
 * 而 SSE 的事件模型 OpenAPI 表达不了 —— 那条路径上的类型**仍然只能手写**，
 * 见 docs/openapi-client.md §11.4。
 */
export type AnswerSource = Schema<'AnswerSource'>;

/** 问答响应（生成自 `QuestionAnswerResponse`） */
export type QuestionAnswerResponse = Schema<'QuestionAnswerResponse'>;

/** 题目生成响应（生成自 `GenerateQuestionsResponse`） */
export type GenerateQuestionsResponse = Schema<'GenerateQuestionsResponse'>;

/**
 * 触发笔记理解管道
 * archived 笔记重新理解会清空旧产物，需 confirm=true 显式确认；
 * 先以 confirm=false 调用可获取 requires_confirm 与影响数量，见 docs/decisions.md#F-02。
 */
export async function startUnderstanding(noteId: string, confirm = false): Promise<UnderstandingStartResponse> {
  return request<UnderstandingStartResponse>(`/understanding/${noteId}/start`, {
    method: 'POST',
    body: JSON.stringify({ confirm }),
    headers: { 'Content-Type': 'application/json' },
  });
}

/**
 * 查询理解状态
 */
export async function getUnderstandingStatus(noteId: string): Promise<UnderstandingStatusResponse> {
  return request<UnderstandingStatusResponse>(`/understanding/${noteId}/status`);
}

/**
 * 获取章节摘要
 */
export async function getChapterSummaries(noteId: string): Promise<ChapterSummaryListResponse> {
  return request(`/understanding/${noteId}/chapters`);
}

/**
 * 获取知识卡片列表
 */
export async function getKnowledgeCards(page = 1, pageSize = 20, noteId?: string, keyword?: string): Promise<KnowledgeCardListResponse> {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (noteId) params.set('note_id', noteId);
  if (keyword) params.set('keyword', keyword);
  return request<KnowledgeCardListResponse>(`/understanding/cards?${params}`);
}

/**
 * 获取知识卡片详情
 */
export async function getKnowledgeCard(cardId: string): Promise<KnowledgeCard> {
  return request<KnowledgeCard>(`/understanding/cards/${cardId}`);
}

/**
 * RAG 问答
 */
export async function askQuestion(question: string): Promise<QuestionAnswerResponse> {
  return request<QuestionAnswerResponse>('/understanding/ask', {
    method: 'POST',
    body: JSON.stringify({ question }),
  });
}

/**
 * 触发题目生成
 */
export async function generateQuestions(noteId: string): Promise<GenerateQuestionsResponse> {
  return request<GenerateQuestionsResponse>(`/understanding/${noteId}/generate-questions`, {
    method: 'POST',
  });
}

/**
 * 获取题目列表
 */
export async function getQuestions(page = 1, pageSize = 20, noteId?: string, keyword?: string): Promise<QuizItemListResponse> {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (noteId) params.set('note_id', noteId);
  if (keyword) params.set('keyword', keyword);
  return request<QuizItemListResponse>(`/understanding/questions?${params}`);
}

/**
 * 获取笔记的卡片去重建议
 */
export async function getCardDuplicates(noteId: string): Promise<CardDuplicateListResponse> {
  return request(`/understanding/${noteId}/duplicates`);
}

/**
 * 更新知识卡片
 */
export async function updateKnowledgeCard(cardId: string, data: { title?: string; content?: string }): Promise<KnowledgeCard> {
  return request<KnowledgeCard>(`/understanding/cards/${cardId}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

/**
 * 删除知识卡片
 */
export async function deleteKnowledgeCard(cardId: string): Promise<void> {
  return request<void>(`/understanding/cards/${cardId}`, {
    method: 'DELETE',
  });
}