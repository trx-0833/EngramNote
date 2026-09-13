/**
 * @file 认证 API
 * @description 登录、注册、登出、当前用户与邮件提醒设置相关接口。
 *
 * 注意：**刷新令牌**的换新不在这里 —— 它是 `client.ts` 里 401 重试链路的一部分
 * （`refreshSession` / `authorizedFetch`），必须绕开 `request()` 的重试包装，
 * 否则刷新失败会递归触发刷新。本文件只放"正常的"API 调用。
 */
import { request, type User, type TokenResponse } from './client'

/**
 * 用户注册
 * 注册成功后自动返回访问令牌与刷新令牌，无需再次登录。
 *
 * @param email - 用户邮箱
 * @param username - 用户名（2-50个字符）
 * @param password - 密码（至少6位）
 * @returns 包含访问令牌、刷新令牌和用户信息的响应
 */
export async function register(email: string, username: string, password: string): Promise<TokenResponse> {
  return request<TokenResponse>('/auth/register', {
    method: 'POST',
    body: JSON.stringify({ email, username, password }),
  });
}

/**
 * 用户登录
 * 使用邮箱和密码进行身份认证，成功后返回一对令牌。
 *
 * @param email - 用户邮箱
 * @param password - 密码
 * @returns 包含访问令牌、刷新令牌和用户信息的响应
 */
export async function login(email: string, password: string): Promise<TokenResponse> {
  return request<TokenResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  });
}

/** 登出结果（阶段 6.3） */
export interface LogoutResult {
  /** 服务端实际撤销的刷新令牌行数（0 表示此前已经无效/已撤销） */
  revoked: number;
}

/**
 * 登出：撤销服务端的刷新令牌（阶段 6.3）
 *
 * 服务端只根据**签名可验证**的刷新令牌动手，因此这里不需要（也无法）
 * 用访问令牌授权；令牌已失效时后端同样返回 200 与 `revoked=0`，
 * 保证"清掉服务端状态"这件事在任何情况下都能被调用。
 *
 * ⚠️ 调用失败（网络异常）**不应该**阻止前端清除本地状态：登出是用户的意图，
 * 本地必须能干净结束（见 AuthContext.logout）。
 *
 * @param refreshToken - 本地保存的刷新令牌；为空表示没有可撤销的目标
 * @param allDevices - 是否撤销该用户的全部刷新令牌（"退出所有设备"）
 * @returns 服务端实际撤销的行数
 */
export async function logout(refreshToken: string | null, allDevices = false): Promise<LogoutResult> {
  return request<LogoutResult>('/auth/logout', {
    method: 'POST',
    body: JSON.stringify({ refresh_token: refreshToken, all_devices: allDevices }),
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