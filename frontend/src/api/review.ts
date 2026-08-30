/**
 * @file 复习调度 API
 * @description 到期题目、提交答案、复习统计与历史、快速复习、复习提醒。
 */
import { request } from './client'

/** 到期题目 */
export interface DueQuiz {
  id: string;
  card_id: string;
  note_id: string;
  question_type: string;
  difficulty: string;
  question: string;
  options: string | null;
  next_review_at: string | null;
  review_count: number;
  interval: number;
  easiness_factor: number;
}

/** 到期题目列表响应 */
export interface DueQuizListResponse {
  items: DueQuiz[];
  total: number;
}

/** SM-2 更新信息 */
export interface SM2Info {
  interval: number;
  repetition: number;
  easiness_factor: number;
  next_review_at: string;
}

/** 提交答案响应 */
export interface SubmitAnswerResponse {
  quiz_id: string;
  is_correct: boolean;
  quality: number;
  correct_answer: string;
  explanation: string | null;
  options: string[] | null;
  question_type: string;
  sm2: SM2Info;
}

/** 复习统计 */
export interface ReviewStats {
  due_count: number;
  today_done: number;
  today_correct: number;
  today_accuracy: number;
  total_reviews: number;
  total_correct: number;
  total_accuracy: number;
  total_quizzes: number;
  /** 每日答题上限（后端单一来源，前端据此显示进度），见 docs/decisions.md#F-12 */
  daily_limit: number;
}

/** 复习历史条目 */
export interface ReviewHistoryItem {
  id: string;
  quiz_id: string;
  note_id: string;
  user_answer: string;
  is_correct: boolean;
  quality: number;
  time_spent_ms: number;
  review_at: string | null;
}

/** 复习历史响应 */
export interface ReviewHistoryResponse {
  items: ReviewHistoryItem[];
  total: number;
  page: number;
  page_size: number;
}

/** 快速复习题目（复用 DueQuiz 类型） */
export type QuickQuiz = DueQuiz

/** 快速复习响应 */
export interface QuickReviewResponse {
  items: QuickQuiz[];
  total: number;
}

/**
 * 获取今日到期复习题目
 */
export async function getDueQuizzes(limit = 50): Promise<DueQuizListResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  return request<DueQuizListResponse>(`/review/due?${params}`);
}

/**
 * 提交答案
 */
export async function submitAnswer(quizId: string, userAnswer: string, timeSpentMs = 0): Promise<SubmitAnswerResponse> {
  return request<SubmitAnswerResponse>('/review/submit', {
    method: 'POST',
    body: JSON.stringify({ quiz_id: quizId, user_answer: userAnswer, time_spent_ms: timeSpentMs }),
  });
}

/**
 * 获取复习统计
 */
export async function getReviewStats(): Promise<ReviewStats> {
  return request<ReviewStats>('/review/stats');
}

/**
 * 获取复习历史
 */
export async function getReviewHistory(page = 1, pageSize = 20): Promise<ReviewHistoryResponse> {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  return request<ReviewHistoryResponse>(`/review/history?${params}`);
}

/**
 * 获取指定笔记的快速复习题目
 * 用于笔记上传并完成理解后，立即复习该笔记关联的所有题目。
 *
 * @param noteId - 笔记 ID
 * @returns 快速复习题目列表
 */
export async function getQuickReview(noteId: string): Promise<QuickReviewResponse> {
  return request<QuickReviewResponse>(`/review/quick/${noteId}`);
}

/**
 * 提交快速复习答案
 * 与 submitAnswer 不同，此接口不受每日复习上限限制，
 * 用于"立即学习"场景。
 *
 * @param noteId - 笔记 ID
 * @param quizId - 题目 ID
 * @param userAnswer - 用户答案
 * @param timeSpentMs - 答题耗时（毫秒）
 * @returns 提交答案响应
 */
export async function submitQuickReviewAnswer(noteId: string, quizId: string, userAnswer: string, timeSpentMs = 0): Promise<SubmitAnswerResponse> {
  return request<SubmitAnswerResponse>(`/review/quick/${noteId}/submit`, {
    method: 'POST',
    body: JSON.stringify({ quiz_id: quizId, user_answer: userAnswer, time_spent_ms: timeSpentMs }),
  });
}

/** 复习提醒响应 */
export interface ReminderResponse {
  /** 当前到期需要复习的题目数 */
  due_count: number;
  /** 1小时内到期的题目数 */
  due_in_1h_count: number;
  /** 薄弱知识点数 */
  weak_point_count: number;
  /** 上次提醒时间 */
  last_reminded_at: string | null;
}

/** 获取复习提醒数据 */
export async function getReminders(): Promise<ReminderResponse> {
  return request<ReminderResponse>('/review/reminders');
}