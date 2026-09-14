/**
 * @file 学习目标 API
 * @description 学习目标的创建、列表、详情、归档、删除与每日计划。
 */
import { request } from './client'
import type { Schema } from './generated/types'

// --- 学习目标相关类型（阶段 5.1 / S2：来源已改为 OpenAPI 生成类型）---

/** 学习目标（生成自 `GoalResponse`；`type` / `status` 是后端枚举） */
export type LearningGoal = Schema<'GoalResponse'>;

/** 目标列表响应（生成自 `GoalListResponse`） */
export type GoalListResponse = Schema<'GoalListResponse'>;

/**
 * 推荐任务
 *
 * ⚠️ **这个类型仍然是手写的**：`DailyPlanResponse.recommended_tasks` 在契约里是
 * `{[key: string]: unknown}`（后端把结构化数据塞进了一个 JSON 列），
 * schema 里**没有** `RecommendedTask` 这个组件 —— 没有可指向的生成类型。
 * 页面对它的规整逻辑（Dashboard / TodayLearn 的 `toRecommendedTasks`）
 * 因此仍然是"手写契约"，见 docs/openapi-client.md §9.2。
 */
export interface RecommendedTask {
  task_type: 'review' | 'new_material' | 'weak_point';
  quiz_id: string | null;
  note_id: string | null;
  card_id: string | null;
  priority: number;
  title: string;
}

/** 每日计划响应（生成自 `DailyPlanResponse`） */
export type DailyPlanResponse = Schema<'DailyPlanResponse'>;

/** 创建学习目标 */
export async function createGoal(data: {
  name: string;
  type?: 'daily' | 'weekly';
  scope_notes?: string[];
  scope_folders?: string[];
  target_mastery?: number;
  deadline?: string;
}): Promise<LearningGoal> {
  return request<LearningGoal>('/goals', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

/** 获取学习目标列表 */
export async function getGoals(status?: string): Promise<GoalListResponse> {
  const query = status ? `?status=${encodeURIComponent(status)}` : '';
  return request<GoalListResponse>(`/goals${query}`);
}

/** 获取单个学习目标详情 */
export async function getGoal(goalId: string): Promise<LearningGoal> {
  return request<LearningGoal>(`/goals/${goalId}`);
}

/** 更新学习目标 */
export async function updateGoal(goalId: string, data: {
  name?: string;
  type?: 'daily' | 'weekly';
  scope_notes?: string[];
  scope_folders?: string[];
  target_mastery?: number;
  deadline?: string;
}): Promise<LearningGoal> {
  return request<LearningGoal>(`/goals/${goalId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

/** 归档学习目标 */
export async function archiveGoal(goalId: string): Promise<LearningGoal> {
  return request<LearningGoal>(`/goals/${goalId}/archive`, {
    method: 'POST',
  });
}

/** 删除学习目标（软删除） */
export async function deleteGoal(goalId: string): Promise<void> {
  return request<void>(`/goals/${goalId}`, {
    method: 'DELETE',
  });
}

/** 获取今日推荐任务 */
export async function getDailyPlan(): Promise<DailyPlanResponse> {
  return request<DailyPlanResponse>('/goals/daily-plan');
}