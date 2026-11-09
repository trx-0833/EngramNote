/**
 * @file 用户设置 API（PC 设置页）
 *
 * 设置页一次读完整快照；`PATCH` 只提交改动段，后端返回合并后的完整快照，
 * 前端直接 `setQueryData`，不需要再补一次 GET。
 */
import { request } from './client';
import type { BodyOf, Schema } from './generated/types';

export type UserSettings = Schema<'SettingsResponse'>;
export type SettingsPatchBody = BodyOf<'/settings', 'patch'>;
export type SettingsUsage = Schema<'SettingsUsageResponse'>;

/** 读取当前用户的完整设置（不写库） */
export async function getSettings(): Promise<UserSettings> {
  return request<UserSettings>('/settings');
}

/** 部分更新；只传要改的字段 */
export async function patchSettings(body: SettingsPatchBody): Promise<UserSettings> {
  return request<UserSettings>('/settings', {
    method: 'PATCH',
    body: JSON.stringify(body),
  });
}

/** 存储用量（与上传前的配额校验同一口径） */
export async function getSettingsUsage(): Promise<SettingsUsage> {
  return request<SettingsUsage>('/settings/usage');
}
