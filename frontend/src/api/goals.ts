/**
 * @file 学习目标 API
 * @description 学习目标的创建、列表、详情、归档、删除与每日计划。
 */
import { request } from './client'

/** 学习目标 */
export interface LearningGoal {
  id: string;
  user_id: string;
  name: string;
  type: 'daily' | 'weekly';
  scope_notes: string[];
  scope_folders: string[];
  target_mastery: number;
  deadline: string | null;
  status: 'active' | 'completed' | 'expired' | 'archived' | 'deleted';
  progress_cache: number;
  last_progress_refresh: string | null;
  progress_percentage: number;
  created_at: string;
  updated_at: string;
}

/** 目标列表响应 */
export interface GoalListResponse {
  goals: LearningGoal[];
  total: number;
}

/** 推荐任务 */
export interface RecommendedTask {
  task_type: 'review' | 'new_material' | 'weak_point';
  quiz_id: string | null;
  note_id: string | null;
  card_id: string | null;
  priority: number;
  title: string;
}

/** 每日计划响应 */
export interface DailyPlanResponse {
  id: string;
  goal_id: string;
  plan_date: string;
  recommended_tasks: Record<string, unknown>;
  completed_count: number;
  total_count: number;
}

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