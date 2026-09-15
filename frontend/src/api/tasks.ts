/**
 * @file 任务进度 API（overhaul-plan 阶段 5.11）
 * @description 后端从阶段 1′ 起就有完整的任务进度契约
 *   （`GET /api/tasks/note/{note_id}` / `POST /api/tasks/{task_id}/cancel`），
 *   但前端**从来没有消费过它** —— 用户看到的始终是一句"正在转换中，请稍候..."，
 *   既不知道跑到哪一步、也不知道能不能停。
 *
 * 单独成文件而不是塞进 `client.ts`：那里是**横切基础设施**（token / 401 刷新单飞 /
 * 超时 / 错误信封），2026-09-14 复核为 413 行、只留 1 个域函数（`askQuestionStream`，
 * SSE 手写层）；按域拆开的 API 函数共 **111 个 / 14 个模块**
 * （附录 BH 更正过"90 个函数"那个数字）。新增接口不该继续往回堆。
 */
import { request } from './client';
import { buildQuery } from './query';
import type { QueryOf, Schema } from './generated/types';

// --- 任务进度相关类型（阶段 5.1 / S2：来源已改为 OpenAPI 生成类型）---

/** 与后端 `TaskRunResponse` 逐字段对应（生成自 `TaskRunResponse`） */
export type TaskRun = Schema<'TaskRunResponse'>;

/** 笔记任务列表响应（生成自 `TaskRunListResponse`） */
export type TaskRunList = Schema<'TaskRunListResponse'>;

/**
 * 取消任务的结果（生成自 `CancelResponse`）
 *
 * `terminated` 表示是否已在服务端**强制终止**：文件系统 broker 下通常为 `false`
 * （任务会在下一个阶段边界自行退出）。前端必须如实展示这一点，
 * 否则用户会以为"点了取消就立刻停了"。
 */
export type CancelResult = Schema<'CancelResponse'>;

/** 终态集合：这些状态不会再有进展，轮询应当停止 */
export const TERMINAL_TASK_STATUSES = ['succeeded', 'failed', 'stale', 'cancelled'];

export function isTerminal(status: string): boolean {
  return TERMINAL_TASK_STATUSES.includes(status);
}

/** 列出某笔记的近期任务（最新在前） */
export function listNoteTasks(noteId: string, limit = 20): Promise<TaskRunList> {
  // 查询参数由契约派生（阶段 5.1 / S3b）：键名写错会在这一行编译失败
  const query: QueryOf<'/tasks/note/{note_id}', 'get'> = { limit };
  return request<TaskRunList>(`/tasks/note/${noteId}${buildQuery(query)}`);
}

/** 查询单个任务 */
export function getTask(taskId: string): Promise<TaskRun> {
  return request<TaskRun>(`/tasks/${taskId}`);
}

/** 请求取消任务（幂等；返回体里的 `terminated` 说明是否真的停了） */
export function cancelTask(taskId: string): Promise<CancelResult> {
  return request<CancelResult>(`/tasks/${taskId}/cancel`, { method: 'POST' });
}
