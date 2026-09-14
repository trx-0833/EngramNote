/**
 * @file 学习报告 API
 * @description 今日报告、7 天趋势与薄弱点列表。
 */
import { request } from './client';
import { buildQuery } from './query';
import type { QueryOf, Schema } from './generated/types';

// --- 报告相关类型（阶段 5.1 / S2：来源已改为 OpenAPI 生成类型）---
//
// `report.ts` 是判定质量最好的一域（3 个函数全部 IDENTICAL），
// 换成生成类型后**逐字段等价**，是这一轮里唯一"零风险"的一域。

/** 各题型正确率（生成自 `QuestionTypeAccuracy`） */
export type QuestionTypeAccuracy = Schema<'QuestionTypeAccuracy'>;

/** 今日学习报告（生成自 `DailyReportResponse`） */
export type DailyReport = Schema<'DailyReportResponse'>;

/** 单日趋势数据（生成自 `WeeklyTrendItem`） */
export type WeeklyTrendItem = Schema<'WeeklyTrendItem'>;

/** 7天趋势响应（生成自 `WeeklyTrendResponse`） */
export type WeeklyTrendResponse = Schema<'WeeklyTrendResponse'>;

/** 薄弱点条目（生成自 `WeakPointItem`） */
export type WeakPoint = Schema<'WeakPointItem'>;

/** 薄弱点列表响应（生成自 `WeakPointsResponse`） */
export type WeakPointsResponse = Schema<'WeakPointsResponse'>;

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
 *
 * 查询参数由契约派生（阶段 5.1 / S3b）：`QueryOf<'/report/weak-points','get'>` 里
 * 只有 `limit` 一个键，写错会在这一行编译失败（迁移前是手拼的 `?limit=`）。
 */
export async function getWeakPoints(limit = 5): Promise<WeakPointsResponse> {
  const query: QueryOf<'/report/weak-points', 'get'> = { limit };
  return request<WeakPointsResponse>(`/report/weak-points${buildQuery(query)}`);
}
