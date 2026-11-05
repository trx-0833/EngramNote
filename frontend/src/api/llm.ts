/** @file LLM 用量（设置页只读展示） */
import { request } from './client';
import type { Schema } from './generated/types';

export type LlmUsage = Schema<'UsageResponse'>;

export async function getLlmUsage(days = 30): Promise<LlmUsage> {
  return request<LlmUsage>(`/llm/usage?days=${days}&group_by=none`);
}
