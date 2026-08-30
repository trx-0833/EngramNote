/**
 * @file 认证 API
 * @description 登录、注册、当前用户与邮件提醒设置相关接口。
 */
import { request, type User, type TokenResponse } from './client'

/**
 * 用户注册
 * 注册成功后自动返回 JWT 令牌，无需再次登录。
 *
 * @param email - 用户邮箱
 * @param username - 用户名（2-50个字符）
 * @param password - 密码（至少6位）
 * @returns 包含访问令牌和用户信息的响应
 */
export async function register(email: string, username: string, password: string): Promise<TokenResponse> {
  return request<TokenResponse>('/auth/register', {
    method: 'POST',
    body: JSON.stringify({ email, username, password }),
  });
}

/**
 * 用户登录
 * 使用邮箱和密码进行身份认证，成功后返回 JWT 令牌。
 *
 * @param email - 用户邮箱
 * @param password - 密码
 * @returns 包含访问令牌和用户信息的响应
 */
export async function login(email: string, password: string): Promise<TokenResponse> {
  return request<TokenResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  });
}

/**
 * 获取当前登录用户信息
 * 需要有效的 JWT 令牌，用于验证令牌是否仍然有效。
 *
 * @returns 当前用户信息
 */
export async function getMe(): Promise<User> {
  return request<User>('/auth/me');
}

// --- 邮件提醒设置 API ---

/** 用户邮件提醒设置 */
export interface UserReminderSettings {
  /** 是否开启邮件复习提醒 */
  email_reminder_enabled: boolean;
}

/**
 * 获取当前用户的邮件提醒设置
 *
 * @returns 邮件复习提醒开关状态
 */
export async function getUserReminderSettings(): Promise<UserReminderSettings> {
  return request<UserReminderSettings>('/auth/reminder-settings');
}

/**
 * 更新当前用户的邮件提醒开关
 *
 * @param enabled - 是否开启邮件复习提醒
 * @returns 更新后的邮件复习提醒设置
 */
export async function updateUserReminderSettings(enabled: boolean): Promise<UserReminderSettings> {
  return request<UserReminderSettings>('/auth/reminder-settings', {
    method: 'PUT',
    body: JSON.stringify({ email_reminder_enabled: enabled }),
  });
}