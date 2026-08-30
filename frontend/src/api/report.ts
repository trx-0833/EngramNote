/**
 * @file 学习报告 API
 * @description 今日报告、7 天趋势与薄弱点列表。
 */
import { request } from './client'

/** 各题型正确率 */
export interface QuestionTypeAccuracy {
  question_type: string;
  total: number;
  correct: number;
  accuracy: number;
}

/** 今日学习报告 */
export interface DailyReport {
  date: string;
  new_mastered: number;
  total_review_time_ms: number;
  total_reviews: number;
  today_accuracy: number;
  weak_point_count: number;
  question_type_accuracy: QuestionTypeAccuracy[];
}

/** 单日趋势数据 */
export interface WeeklyTrendItem {
  date: string;
  review_count: number;
  correct_count: number;
  accuracy: number;
}

/** 7天趋势响应 */
export interface WeeklyTrendResponse {
  items: WeeklyTrendItem[];
  total_reviews: number;
  avg_accuracy: number;
}

/** 薄弱点条目 */
export interface WeakPoint {
  card_id: string;
  card_title: string;
  card_type: string;
  note_id: string;
  note_title: string;
  error_count: number;
  total_reviews: number;
  accuracy: number;
}

/** 薄弱点列表响应 */
export interface WeakPointsResponse {
  items: WeakPoint[];
  total: number;
}

/**
 * 获取今日学习报告
 */
export async function getDailyReport(): Promise<DailyReport> {
  return request<DailyReport>('/report/daily');
}

/**
 * 获取7天复习趋势
 */
export async function getWeeklyTrend(): Promise<WeeklyTrendResponse> {
  return request<WeeklyTrendResponse>('/report/weekly-trend');
}

/**
 * 获取薄弱点列表
 */
export async function getWeakPoints(limit = 5): Promise<WeakPointsResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  return request<WeakPointsResponse>(`/report/weak-points?${params}`);
}