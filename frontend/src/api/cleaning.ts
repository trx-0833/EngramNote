/**
 * @file 清洗 API
 * @description 笔记清洗触发、停止、状态查询、diff 对比与重复块操作。
 */
import { request } from './client'

/** 清洗触发响应 */
export interface CleaningStartResponse {
  /** 笔记 ID */
  id: string;
  /** 当前状态 */
  status: string;
  /** 提示信息 */
  message: string;
}

/** 停止清洗响应 */
export interface CleaningStopResponse {
  /** 笔记 ID */
  id: string;
  /** 当前状态 */
  status: string;
  /** 提示信息 */
  message: string;
}

/** 清洗状态响应 */
export interface CleaningStatusResponse {
  /** 笔记 ID */
  id: string;
  /** 当前状态 */
  status: string;
  /** 清洗后 Markdown 路径 */
  clean_md_path: string | null;
  /** 错误信息 */
  error_message: string | null;
  /** 元数据（含清洗统计） */
  metadata_: Record<string, unknown> | null;
}

/** 单行 diff 数据 */
export interface DiffLine {
  /** 行类型：added（新增）、removed（删除）、unchanged（未变） */
  type: 'added' | 'removed' | 'unchanged';
  /** 行内容 */
  content: string;
  /** 原始版行号 */
  line_number_original: number | null;
  /** 清洗版行号 */
  line_number_clean: number | null;
}

/** diff 块数据（连续的变更行） */
export interface DiffBlock {
  /** 块内的行列表 */
  lines: DiffLine[];
}

/** 清洗 diff 响应 */
export interface CleaningDiffResponse {
  /** 笔记 ID */
  note_id: string;
  /** 原始版行数 */
  original_lines: number;
  /** 清洗版行数 */
  clean_lines: number;
  /** diff 块列表 */
  blocks: DiffBlock[];
  /** 清洗统计信息 */
  stats: Record<string, number> | null;
}

/** 块操作响应（恢复/删除） */
export interface BlockOperationResponse {
  /** 笔记 ID */
  note_id: string;
  /** 块序号 */
  block_index: number;
  /** 操作类型 */
  operation: 'restored' | 'deleted';
  /** 提示信息 */
  message: string;
}

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
export async function restoreBlock(noteId: string, blockIndex: number): Promise<BlockOperationResponse> {
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
export async function deleteBlock(noteId: string, blockIndex: number): Promise<BlockOperationResponse> {
  return request<BlockOperationResponse>(`/cleaning/${noteId}/block/${blockIndex}`, {
    method: 'DELETE',
  });
}