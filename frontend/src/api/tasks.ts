/**
 * @file 任务进度 API（overhaul-plan 阶段 5.11）
 * @description 后端从阶段 1′ 起就有完整的任务进度契约
 *   （`GET /api/tasks/note/{note_id}` / `POST /api/tasks/{task_id}/cancel`），
 *   但前端**从来没有消费过它** —— 用户看到的始终是一句"正在转换中，请稍候..."，
 *   既不知道跑到哪一步、也不知道能不能停。
 *
 * 单独成文件而不是塞进 `client.ts`：那里已经 330 行、90 个函数混在一起，
 * 是这个项目正在被逐步拆掉的形态；新增的接口不该继续往那里堆。
 */
import { request } from './client';

/** 与后端 `TaskRunResponse` 逐字段对应 */
export interface TaskRun {
  task_id: string;
  task_name: string;
  note_id: string | null;
  /** `pending` / `running` / `succeeded` / `failed` / `stale` / `cancelled` */
  status: string;
  /** 0.0 ~ 1.0 */
  progress: number;
  /** 可直接展示的阶段名（如"正在抽取知识点"），可能为空字符串 */
  stage: string;
  message: string | null;
  attempt: number;
  max_attempts: number;
  error: string | null;
  retryable: boolean;
  heartbeat_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface TaskRunList {
  items: TaskRun[];
  total: number;
}

export interface CancelResult {
  task_id: string;
  status: string;
  /**
   * 是否已在服务端**强制终止**。
   *
   * 文件系统 broker 下通常为 `false`：任务会在下一个阶段边界自行退出。
   * 前端必须如实展示这一点，否则用户会以为"点了取消就立刻停了"。
   */
  terminated: boolean;
  detail: string;
}

/** 终态集合：这些状态不会再有进展，轮询应当停止 */
export const TERMINAL_TASK_STATUSES = ['succeeded', 'failed', 'stale', 'cancelled'];

export function isTerminal(status: string): boolean {
  return TERMINAL_TASK_STATUSES.includes(status);
}

/** 列出某笔记的近期任务（最新在前） */
export function listNoteTasks(noteId: string, limit = 20): Promise<TaskRunList> {
  return request<TaskRunList>(`/tasks/note/${noteId}?limit=${limit}`);
}

/** 查询单个任务 */
export function getTask(taskId: string): Promise<TaskRun> {
  return request<TaskRun>(`/tasks/${taskId}`);
}

/** 请求取消任务（幂等；返回体里的 `terminated` 说明是否真的停了） */
export function cancelTask(taskId: string): Promise<CancelResult> {
  return request<CancelResult>(`/tasks/${taskId}/cancel`, { method: 'POST' });
}
