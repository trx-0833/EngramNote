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

/** 调度结果（字段名 `sm2` 是历史遗留：阶段 3.6 之后默认由 FSRS-5 产生） */
export interface SM2Info {
  interval: number;
  repetition: number;
  easiness_factor: number;
  /** 占位提交（等待用户自评）时不推进调度，此处为 null */
  next_review_at: string | null;
  /** FSRS 评分档位 1-4；null = 本次未推进调度，或走的是 SM-2 回退路径 */
  rating?: number | null;
  /**
   * **复习前**模型预测的可回忆概率（0-1）。
   * 它解释间隔为什么是这个数：0.6 表示模型认为你已接近遗忘，
   * 因此这次答对后间隔会涨得更多。SM-2 回退路径下为 null。
   */
  predicted_retention?: number | null;
}

/** 判分方式：choice/fill_blank 为可靠的自动判分，self_rating 为用户自评 */
export type GradingMethod = 'choice' | 'fill_blank' | 'self_rating' | 'ungraded' | 'legacy';

/**
 * LLM 语义判分明细（阶段 3.5）
 *
 * 关键设计：**不是 0-100 分，而是"缺了哪一点、误解了哪一点"** ——
 * 前者无法校准也没有指导价值，后者可展示、可核对。
 */
export interface GradingDetail {
  verdict: 'correct' | 'partial' | 'incorrect';
  missing_points?: string[];
  misconceptions?: string[];
  confidence?: number;
  reason?: string;
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
  /** 本次提交携带的自评分；未自评时为 null */
  self_rating: number | null;
  grading_method: GradingMethod;
  /** 仍在等待用户自评：自动判分不可信且今日尚未自评。UI 据此展示四档自评 */
  needs_self_assessment: boolean;
  /** 本次提交是否补完了此前的占位记录 */
  completing_placeholder: boolean;
  /** 判分依据说明，用于向用户解释判分可信度 */
  grading_reason: string | null;
  /**
   * 语义判分明细；`null` = 本次**没有**语义判分（未请求 / 判分失败 / 已自评）
   *
   * ⚠️ 不要把 null 当成"判分过但没发现问题" —— 那是两回事，
   * 后者应当显示"没有发现遗漏"，前者应当什么都不显示。
   */
  grading_detail?: GradingDetail | null;
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
 *
 * 两阶段提交：简答题的自动判分不可信，第一次调用不传 selfRating 只会落一条
 * 占位记录、不推进调度（响应 needs_self_assessment=true）；用户四档自评后
 * 再次调用并传入 selfRating，才真正完成判分与调度。
 *
 * @param selfRating - 用户自评的 SM-2 质量分（0-5），不传表示本次不自评
 * @param useSemanticGrading - 是否请求 LLM 语义判分（阶段 3.5，仅简答题有意义）。
 *   **默认关闭**：判分在提交的同步路径上调用外部 LLM，会给每次提交叠加一次
 *   往返延迟，而两阶段流程本来就以用户自评为主评分来源（理由见后端
 *   `SubmitAnswerRequest.use_semantic_grading`）。只有首次提交需要它 ——
 *   带 `selfRating` 的那次自评优先，后端会直接跳过 LLM。
 */
export async function submitAnswer(
  quizId: string,
  userAnswer: string,
  timeSpentMs = 0,
  selfRating?: number,
  useSemanticGrading = false,
): Promise<SubmitAnswerResponse> {
  return request<SubmitAnswerResponse>('/review/submit', {
    method: 'POST',
    body: JSON.stringify({
      quiz_id: quizId,
      user_answer: userAnswer,
      time_spent_ms: timeSpentMs,
      ...(selfRating === undefined ? {} : { self_rating: selfRating }),
      ...(useSemanticGrading ? { use_semantic_grading: true } : {}),
    }),
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
 * @param selfRating - 用户自评的 SM-2 质量分（0-5），见 submitAnswer 的两阶段说明
 * @param useSemanticGrading - 是否请求 LLM 语义判分，见 submitAnswer 的说明
 * @returns 提交答案响应
 */
export async function submitQuickReviewAnswer(
  noteId: string,
  quizId: string,
  userAnswer: string,
  timeSpentMs = 0,
  selfRating?: number,
  useSemanticGrading = false,
): Promise<SubmitAnswerResponse> {
  return request<SubmitAnswerResponse>(`/review/quick/${noteId}/submit`, {
    method: 'POST',
    body: JSON.stringify({
      quiz_id: quizId,
      user_answer: userAnswer,
      time_spent_ms: timeSpentMs,
      ...(selfRating === undefined ? {} : { self_rating: selfRating }),
      ...(useSemanticGrading ? { use_semantic_grading: true } : {}),
    }),
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