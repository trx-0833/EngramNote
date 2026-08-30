/**
 * @file 理解管道与问答 API
 * @description 触发理解、状态查询、章节摘要、知识卡片、题目生成与 RAG 问答。
 */
import { request } from './client'

/** 知识卡片 */
export interface KnowledgeCard {
  id: string;
  user_id: string;
  note_id: string;
  note_title: string;
  card_type: string;
  title: string;
  content: string;
  summary: string | null;
  chapter_title: string | null;
  source_text: string | null;
  metadata_: Record<string, unknown> | null;
  card_category: 'regular' | 'blind_spot' | 'extension';
  is_key_point: boolean;
  is_difficulty: boolean;
  mastery_level: number;
  source_note_ids: string[] | null;
  parent_card_id: string | null;
  created_at: string;
  updated_at: string;
}

/** 知识卡片列表响应 */
export interface KnowledgeCardListResponse {
  items: KnowledgeCard[];
  total: number;
  page: number;
  page_size: number;
}

/** 题目 */
export interface QuizItem {
  id: string;
  user_id: string;
  card_id: string;
  note_id: string;
  note_title: string;
  question_type: string;
  difficulty: string;
  question: string;
  answer: string;
  options: string | null;
  explanation: string | null;
  metadata_: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

/** 题目列表响应 */
export interface QuizItemListResponse {
  items: QuizItem[];
  total: number;
  page: number;
  page_size: number;
}

/** 理解管道触发响应 */
export interface UnderstandingImpact {
  cards: number;
  quizzes: number;
  review_logs: number;
  relations: number;
}

export interface UnderstandingStartResponse {
  id: string;
  status: string;
  message: string;
  /** archived 笔记未确认时返回 true，需用户二次确认后带 confirm=true 重调，见 docs/decisions.md#F-02 */
  requires_confirm?: boolean;
  impact?: UnderstandingImpact | null;
}

/** 理解管道状态响应 */
export interface UnderstandingStatusResponse {
  id: string;
  status: string;
  error_message: string | null;
}

/** 章节摘要 */
export interface ChapterSummary {
  chapter_index: number;
  chapter_title: string;
  summary: string;
  card_count: number;
}

/** 问答请求 */
export interface QuestionRequest {
  question: string;
}

/** 问答引用来源 */
export interface AnswerSource {
  note_id: string;
  note_title: string;
  chapter_title: string | null;
  relevant_text: string;
}

/** 问答响应 */
export interface QuestionAnswerResponse {
  question: string;
  answer: string;
  sources: AnswerSource[];
  provider: string;
  /** 检索降级状态：full_vector / hybrid / bm25_only */
  retrieval_status?: string;
}

/** 题目生成响应 */
export interface GenerateQuestionsResponse {
  note_id: string;
  message: string;
  question_count: number;
}

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
export async function getChapterSummaries(noteId: string): Promise<{ note_id: string; chapters: ChapterSummary[] }> {
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
export async function getCardDuplicates(noteId: string): Promise<{ duplicates: Array<{ card_id: string; card_title: string; existing_card_id: string; existing_title: string; similarity: number }> }> {
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