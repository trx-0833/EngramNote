/**
 * @file 清洗 API
 * @description 笔记清洗触发、停止、状态查询、diff 对比与重复块操作。
 */
import { request } from './client';
import type { Schema } from './generated/types';

// --- 清洗相关类型（阶段 5.1 / S2：来源已改为 OpenAPI 生成类型）---

/** 清洗触发响应（生成自 `CleaningStartResponse`；`status` 现在是 `NoteStatus` 枚举） */
export type CleaningStartResponse = Schema<'CleaningStartResponse'>;

/** 停止清洗响应（生成自 `CleaningStopResponse`；`status` 现在是 `NoteStatus` 枚举） */
export type CleaningStopResponse = Schema<'CleaningStopResponse'>;

/**
 * 清洗状态响应（生成自 `CleaningStatusResponse`）
 *
 * ⚠️ 契约把 `clean_md_path` / `error_message` / `metadata_` 声明为
 * `?: T | null`（可缺省**且**可空），不是"必有但可能是 null"。
 */
export type CleaningStatusResponse = Schema<'CleaningStatusResponse'>;

/**
 * 单行 diff 数据（生成自 `DiffLine`）
 *
 * ⚠️ `type` 在契约里是 `string`，此前前端手写成
 * `'added' | 'removed' | 'unchanged'` —— 切换后**前端反而变宽了**。
 * 这是"后端没把这三个值声明成枚举"的直接后果，不是前端写错。
 */
export type DiffLine = Schema<'DiffLine'>;

/** diff 块数据（连续的变更行；生成自 `DiffBlock`） */
export type DiffBlock = Schema<'DiffBlock'>;

/** 清洗 diff 响应（生成自 `CleaningDiffResponse`） */
export type CleaningDiffResponse = Schema<'CleaningDiffResponse'>;

/**
 * 块操作响应（恢复/删除；生成自 `BlockOperationResponse`）
 *
 * 同 `DiffLine.type`：`operation` 在契约里也是 `string`，
 * 手写的 `'restored' | 'deleted'` 联合因此被放宽。
 */
export type BlockOperationResponse = Schema<'BlockOperationResponse'>;

/**
 * 手动触发笔记清洗
 * 仅对 converted、cleaned 或 cleaning_failed 状态的笔记有效。
 *
 * @param noteId - 笔记 ID
 * @returns 清洗触发响应
 */
export async function startCleaning(noteId: string): Promise<CleaningStartResponse> {
  return request<CleaningStartResponse>(`/cleaning/${noteId}/start`, {
    method: 'POST',
  });
}

/**
 * 停止正在进行的清洗任务
 * 仅对 cleaning 状态的笔记有效，将状态更新为 cleaning_failed。
 *
 * @param noteId - 笔记 ID
 * @returns 停止清洗响应
 */
export async function stopCleaning(noteId: string): Promise<CleaningStopResponse> {
  return request<CleaningStopResponse>(`/cleaning/${noteId}/stop`, {
    method: 'POST',
  });
}

/**
 * 查询笔记清洗状态
 * 返回笔记的当前状态、清洗文件路径和元数据。
 *
 * @param noteId - 笔记 ID
 * @returns 清洗状态信息
 */
export async function getCleaningStatus(noteId: string): Promise<CleaningStatusResponse> {
  return request<CleaningStatusResponse>(`/cleaning/${noteId}/status`);
}

/**
 * 获取原始版与清洗版的 diff 数据
 * 返回结构化的行级差异数据，供前端渲染对比视图。
 *
 * @param noteId - 笔记 ID
 * @returns diff 数据
 */
export async function getCleaningDiff(noteId: string): Promise<CleaningDiffResponse> {
  return request<CleaningDiffResponse>(`/cleaning/${noteId}/diff`);
}

/**
 * 恢复被标记为重复的块
 * 移除指定块的 duplicate 注释标记，使其内容正常显示。
 *
 * @param noteId - 笔记 ID
 * @param blockIndex - 要恢复的块序号
 * @returns 操作结果
 */
export async function restoreBlock(
  noteId: string,
  blockIndex: number,
): Promise<BlockOperationResponse> {
  return request<BlockOperationResponse>(`/cleaning/${noteId}/restore/${blockIndex}`, {
    method: 'POST',
  });
}

/**
 * 彻底删除被标记为重复的块
 * 连同内容和注释标记一起删除，不可恢复。
 *
 * @param noteId - 笔记 ID
 * @param blockIndex - 要删除的块序号
 * @returns 操作结果
 */
export async function deleteBlock(
  noteId: string,
  blockIndex: number,
): Promise<BlockOperationResponse> {
  return request<BlockOperationResponse>(`/cleaning/${noteId}/block/${blockIndex}`, {
    method: 'DELETE',
  });
}
